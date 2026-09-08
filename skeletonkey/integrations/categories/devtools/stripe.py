"""Stripe Integration for SkeletonKey (P5.5).

REST client for the Stripe API (https://docs.stripe.com/api):

- Balance, charges and refunds
- Customers (read + write)
- Payment intents (read + create)
- Invoices, products and prices (read)

Zero mandatory dependencies (ADR-0001): the transport is stdlib
``urllib.request``. The official ``stripe`` package is an optional accelerator;
it is recorded in health metadata when installed but the wire path is identical
either way.

Auth: Stripe's API has no anonymous access, so a secret key is required for
every call. It resolves from config (``token``/``api_key``),
``integrations.secrets`` (``token``/``stripe_secret_key``), or the
``STRIPE_SECRET_KEY`` / ``STRIPE_API_KEY`` environment variables.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import urllib.parse
from typing import Any

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.integrations import (
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationPlatform,
    IntegrationType,
    RestIntegration,
    register_integration,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.stripe.com/v1"
USER_AGENT = "skeletonkey-toolforge"


@register_integration(
    name="stripe",
    title="Stripe",
    version="1.0.0",
    integration_type=IntegrationType.BILLING_PAYMENT,
    category=IntegrationCategory.PAYMENT,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="Stripe API client (charges, customers, payment intents, invoices)",
    author="SkeletonKey Team",
    license="Apache-2.0",
    keywords=["stripe", "payments", "billing", "customers", "invoices"],
    dependencies=[
        IntegrationDependency(
            name="stripe",
            version=">=9.0.0",
            optional=True,
            description="Stripe Python SDK - optional accelerator, not required",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to api.stripe.com",
            required=True,
            default_granted=False,
        ),
    ],
)
class StripeIntegration(RestIntegration):
    """Stripe REST API integration.

    Stripe uses form-encoded request bodies (not JSON) and OAuth-free Bearer
    auth. Every response is the upstream JSON payload, with the raw request id
    surfaced as ``_request_id`` when Stripe returns one.
    """

    def __init__(self, config: IntegrationConfig):
        """Initialize the Stripe integration."""
        super().__init__(config)
        self._base_url: str = str(
            config.config.get("base_url") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout: float = float(config.config.get("timeout", 30))
        self._api_key: str | None = None
        self._has_sdk = False

    # ------------------------------------------------------------------ lifecycle
    def initialize(self) -> bool:
        """Resolve the secret key and note whether the optional SDK is installed."""
        self._api_key = self._resolve_key()
        self._has_sdk = importlib.util.find_spec("stripe") is not None
        self._initialized = True
        logger.info(
            "Stripe integration initialized (base=%s, key=%s, sdk=%s)",
            self._base_url,
            "set" if self._api_key else "unset",
            self._has_sdk,
        )
        return True

    def cleanup(self) -> None:
        """Release the in-memory secret key."""
        self._api_key = None
        self._initialized = False

    def check_health(self) -> tuple[str, str]:
        """Reachability check; does not consume or create anything."""
        if not self._initialized:
            return ("unhealthy", "Integration not initialized")
        if not self._api_key:
            return ("unhealthy", "Stripe secret key not configured")
        try:
            resp = self._request("GET", "/balance")
            if resp["status"] < 400:
                return ("healthy", "Stripe API is reachable and authenticated")
            return ("degraded", f"Stripe API returned HTTP {resp['status']}")
        except SkeletonKeyError as e:
            return ("degraded", str(e))

    # ------------------------------------------------------------------ config
    def _resolve_key(self) -> str | None:
        """Secret key precedence: secrets -> config -> environment."""
        for key in ("token", "stripe_secret_key", "api_key", "secret_key"):
            value = self.config.secrets.get(key) or self.config.config.get(key)
            if value:
                return str(value)
        return os.environ.get("STRIPE_SECRET_KEY") or os.environ.get("STRIPE_API_KEY")

    def _require_key(self) -> str:
        """Return the secret key or raise a usage error naming the fix."""
        if not self._api_key:
            raise SkeletonKeyError(
                E.BAD_ARGS,
                "Stripe secret key required",
                details={
                    "field": "token",
                    "sources": ["STRIPE_SECRET_KEY", "STRIPE_API_KEY", "config.token"],
                },
                hint="Set STRIPE_SECRET_KEY or pass config={'token': ...} on integrations.load.",
            )
        return self._api_key

    # ------------------------------------------------------------------ transport
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._require_key()}"}

    def _encode_body(self, method: str, body: Any):
        if body is None or method.upper() in {"GET", "HEAD"}:
            return None, {}
        return urllib.parse.urlencode(_flatten(body)).encode("utf-8"), {
            "Content-Type": "application/x-www-form-urlencoded"
        }

    def _remote_error(self, response: dict[str, Any]):
        detail = response["data"]
        error = detail.get("error") if isinstance(detail, dict) else None
        if isinstance(error, dict):
            return (
                error.get("message") or str(detail),
                {"stripe_code": error.get("code"), "type": error.get("type")},
            )
        return (str(detail or "remote request failed"), {"body": detail})

    def _request(self, method: str, path: str, *, query=None, params=None, body=None):
        return super()._request(method, path, query=query, body=body, params=params)

    def _envelope(self, resp: dict[str, Any]) -> dict[str, Any]:
        data = resp["data"]
        request_id = resp["headers"].get("request-id")
        if isinstance(data, dict) and request_id:
            data = dict(data)
            data["_request_id"] = request_id
        return data

    def _paged(self, resp: dict[str, Any]) -> dict[str, Any]:
        data = resp["data"]
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return {
                "items": data["data"],
                "count": len(data["data"]),
                "has_more": bool(data.get("has_more", False)),
                "_request_id": resp["headers"].get("request-id"),
            }
        return self._envelope(resp)

    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "stripe.balance",
                "title": "Retrieve Balance",
                "description": "Retrieve the account balance",
                "capability": "stripe.balance",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["stripe", "balance", "billing"],
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "id": "stripe.charges.list",
                "title": "List Charges",
                "description": "List charges (newest first)",
                "capability": "stripe.charges.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["stripe", "charges", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                        "customer": {"type": "string"},
                    },
                },
            },
            {
                "id": "stripe.charges.retrieve",
                "title": "Retrieve Charge",
                "description": "Retrieve a single charge by id",
                "capability": "stripe.charges.retrieve",
                "risk": "read",
                "typical_latency_ms": 300,
                "tier": "task",
                "tags": ["stripe", "charges", "get"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "charge_id": {"type": "string", "minLength": 1},
                    },
                    "required": ["charge_id"],
                },
            },
            {
                "id": "stripe.charges.create",
                "title": "Create Charge",
                "description": "Create a charge (amount is in the smallest currency unit)",
                "capability": "stripe.charges.create",
                "risk": "write",
                "typical_latency_ms": 600,
                "tier": "task",
                "tags": ["stripe", "charges", "create", "write"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer", "minimum": 1},
                        "currency": {"type": "string", "minLength": 3, "maxLength": 3},
                        "source": {"type": "string"},
                        "customer": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["amount", "currency"],
                },
            },
            {
                "id": "stripe.customers.list",
                "title": "List Customers",
                "description": "List customers (newest first)",
                "capability": "stripe.customers.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["stripe", "customers", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                        "email": {"type": "string"},
                    },
                },
            },
            {
                "id": "stripe.customers.retrieve",
                "title": "Retrieve Customer",
                "description": "Retrieve a single customer by id",
                "capability": "stripe.customers.retrieve",
                "risk": "read",
                "typical_latency_ms": 300,
                "tier": "task",
                "tags": ["stripe", "customers", "get"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "minLength": 1},
                    },
                    "required": ["customer_id"],
                },
            },
            {
                "id": "stripe.customers.create",
                "title": "Create Customer",
                "description": "Create a new customer",
                "capability": "stripe.customers.create",
                "risk": "write",
                "typical_latency_ms": 500,
                "tier": "task",
                "tags": ["stripe", "customers", "create", "write"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "metadata": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                },
            },
            {
                "id": "stripe.payment_intents.create",
                "title": "Create Payment Intent",
                "description": "Create a payment intent (amount in the smallest currency unit)",
                "capability": "stripe.payment_intents.create",
                "risk": "write",
                "typical_latency_ms": 600,
                "tier": "task",
                "tags": ["stripe", "payment-intents", "create", "write"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer", "minimum": 1},
                        "currency": {"type": "string", "minLength": 3, "maxLength": 3},
                        "customer": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["amount", "currency"],
                },
            },
            {
                "id": "stripe.payment_intents.retrieve",
                "title": "Retrieve Payment Intent",
                "description": "Retrieve a single payment intent by id",
                "capability": "stripe.payment_intents.retrieve",
                "risk": "read",
                "typical_latency_ms": 300,
                "tier": "task",
                "tags": ["stripe", "payment-intents", "get"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "intent_id": {"type": "string", "minLength": 1},
                    },
                    "required": ["intent_id"],
                },
            },
            {
                "id": "stripe.invoices.list",
                "title": "List Invoices",
                "description": "List invoices",
                "capability": "stripe.invoices.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["stripe", "invoices", "list", "billing"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                        "status": {"type": "string", "enum": ["draft", "open", "paid", "void", "uncollectible"]},
                        "customer": {"type": "string"},
                    },
                },
            },
            {
                "id": "stripe.products.list",
                "title": "List Products",
                "description": "List products",
                "capability": "stripe.products.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["stripe", "products", "list", "billing"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                        "active": {"type": "boolean"},
                    },
                },
            },
            {
                "id": "stripe.prices.list",
                "title": "List Prices",
                "description": "List prices",
                "capability": "stripe.prices.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["stripe", "prices", "list", "billing"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                        "active": {"type": "boolean"},
                    },
                },
            },
        ]

    # ------------------------------------------------------------------ tool impls
    def tool_balance(self) -> dict[str, Any]:
        """Retrieve the account balance."""
        return self._envelope(self._api("GET", "/balance"))

    def tool_charges_list(
        self,
        limit: int = 10,
        customer: str | None = None,
    ) -> dict[str, Any]:
        """List charges."""
        resp = self._api("GET", "/charges", query={"limit": limit, "customer": customer})
        return self._paged(resp)

    def tool_charges_retrieve(self, charge_id: str) -> dict[str, Any]:
        """Retrieve a charge."""
        return self._envelope(self._api("GET", f"/charges/{_seg(charge_id)}"))

    def tool_charges_create(
        self,
        amount: int,
        currency: str,
        source: str | None = None,
        customer: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a charge."""
        params: dict[str, Any] = {"amount": amount, "currency": currency}
        if source:
            params["source"] = source
        if customer:
            params["customer"] = customer
        if description:
            params["description"] = description
        return self._envelope(self._api("POST", "/charges", params=params))

    def tool_customers_list(
        self,
        limit: int = 10,
        email: str | None = None,
    ) -> dict[str, Any]:
        """List customers."""
        resp = self._api("GET", "/customers", query={"limit": limit, "email": email})
        return self._paged(resp)

    def tool_customers_retrieve(self, customer_id: str) -> dict[str, Any]:
        """Retrieve a customer."""
        return self._envelope(self._api("GET", f"/customers/{_seg(customer_id)}"))

    def tool_customers_create(
        self,
        email: str | None = None,
        name: str | None = None,
        description: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a customer."""
        params: dict[str, Any] = {}
        if email:
            params["email"] = email
        if name:
            params["name"] = name
        if description:
            params["description"] = description
        if metadata:
            for key, value in metadata.items():
                params[f"metadata[{key}]"] = value
        return self._envelope(self._api("POST", "/customers", params=params))

    def tool_payment_intents_create(
        self,
        amount: int,
        currency: str,
        customer: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a payment intent."""
        params: dict[str, Any] = {"amount": amount, "currency": currency}
        if customer:
            params["customer"] = customer
        if description:
            params["description"] = description
        return self._envelope(self._api("POST", "/payment_intents", params=params))

    def tool_payment_intents_retrieve(self, intent_id: str) -> dict[str, Any]:
        """Retrieve a payment intent."""
        return self._envelope(self._api("GET", f"/payment_intents/{_seg(intent_id)}"))

    def tool_invoices_list(
        self,
        limit: int = 10,
        status: str | None = None,
        customer: str | None = None,
    ) -> dict[str, Any]:
        """List invoices."""
        resp = self._api(
            "GET", "/invoices", query={"limit": limit, "status": status, "customer": customer}
        )
        return self._paged(resp)

    def tool_products_list(
        self,
        limit: int = 10,
        active: bool | None = None,
    ) -> dict[str, Any]:
        """List products."""
        resp = self._api("GET", "/products", query={"limit": limit, "active": active})
        return self._paged(resp)

    def tool_prices_list(
        self,
        limit: int = 10,
        active: bool | None = None,
    ) -> dict[str, Any]:
        """List prices."""
        resp = self._api("GET", "/prices", query={"limit": limit, "active": active})
        return self._paged(resp)


def _seg(value: str) -> str:
    """URL-encode a single path segment."""
    return urllib.parse.quote(str(value), safe="")


def _flatten(params: dict[str, Any]) -> dict[str, str]:
    """Flatten nested list values into Stripe's ``key[]`` form-encoding."""
    out: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                out[f"{key}[{i}]"] = str(item)
        else:
            out[key] = str(value)
    return out
