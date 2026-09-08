"""AWS DevKit Integration Example.

This demonstrates a platform-specific devkit integration for AWS services.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import (
    Integration,
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationPlatform,
    IntegrationType,
    register_integration,
)

logger = logging.getLogger(__name__)


@register_integration(
    name="aws-devkit",
    title="AWS DevKit",
    version="1.0.0",
    integration_type=IntegrationType.PLATFORM_DEVKIT,
    category=IntegrationCategory.CLOUD,
    platform=IntegrationPlatform.AWS,
    description="Integration with AWS services including S3, EC2, Lambda, and more",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["aws", "s3", "ec2", "lambda", "cloud", "amazon", "devkit"],
    dependencies=[
        IntegrationDependency(
            name="boto3",
            version="^1.28.0",
            description="AWS SDK for Python",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to AWS APIs",
            required=True,
        ),
        IntegrationCapability(
            name="filesystem",
            description="Requires filesystem access for credentials",
            required=True,
        ),
    ],
)
class AWSDevKitIntegration(Integration):
    """Integration for AWS services.
    
    This integration provides tools for:
    - S3 bucket operations
    - EC2 instance management
    - Lambda function management
    - IAM operations
    - CloudFormation
    - And more AWS services
    """
    
    def __init__(self, config: IntegrationConfig):
        super().__init__(config)
        self._boto3_client: Any = None
        self._region = config.config.get("region", "us-east-1")
        self._profile = config.config.get("profile", "default")
        self._services: dict[str, Any] = {}
    
    def initialize(self) -> bool:
        """Initialize the AWS DevKit integration."""
        try:
            # Import boto3
            import boto3
            from botocore.exceptions import NoCredentialsError, PartialCredentialsError
            
            # Create a session
            session = boto3.Session(
                profile_name=self._profile,
                region_name=self._region,
            )
            
            # Test credentials
            try:
                sts = session.client('sts')
                identity = sts.get_caller_identity()
                logger.info(f"AWS DevKit initialized with identity: {identity['Arn']}")
            except (NoCredentialsError, PartialCredentialsError) as e:
                logger.warning(f"AWS credentials not configured: {e}")
                # Still allow initialization, but mark as needing credentials
                self._boto3_client = session
                self._initialized = True
                return True
            
            self._boto3_client = session
            self._initialized = True
            
            logger.info(f"AWS DevKit integration initialized (region={self._region}, profile={self._profile})")
            return True
            
        except ImportError as e:
            logger.error(f"boto3 not installed: {e}")
            self._error = "boto3 package is required. Install with: pip install boto3"
            return False
        except Exception as e:
            logger.error(f"Failed to initialize AWS DevKit: {e}")
            self._error = str(e)
            return False
    
    def cleanup(self) -> None:
        """Clean up the AWS DevKit integration."""
        # Close all service clients
        for service_name, client in self._services.items():
            try:
                client.close()
            except Exception as e:
                logger.warning(f"Error closing {service_name} client: {e}")
        
        self._services = {}
        self._boto3_client = None
        self._initialized = False
        logger.info("AWS DevKit integration cleaned up")
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the AWS DevKit integration."""
        if not self._initialized or not self._boto3_client:
            return ("unhealthy", "Integration not initialized")
        
        try:
            # Check if we can access STS
            sts = self._get_service('sts')
            if sts:
                identity = sts.get_caller_identity()
                return ("healthy", f"AWS access verified: {identity['Arn']}")
            return ("healthy", "AWS DevKit is ready")
        except Exception as e:
            return ("unhealthy", f"AWS access check failed: {e}")
    
    def _get_service(self, service_name: str) -> Any:
        """Get or create a boto3 service client."""
        if service_name not in self._services:
            if self._boto3_client:
                self._services[service_name] = self._boto3_client.client(service_name)
        return self._services.get(service_name)
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "aws.s3.list_buckets",
                "name": "AWS S3 List Buckets",
                "description": "List all S3 buckets",
                "category": "storage",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "id": "aws.s3.list_objects",
                "name": "AWS S3 List Objects",
                "description": "List objects in an S3 bucket",
                "category": "storage",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "bucket": {
                            "type": "string",
                            "description": "Name of the S3 bucket",
                        },
                        "prefix": {
                            "type": "string",
                            "description": "Prefix to filter objects",
                        },
                        "max_keys": {
                            "type": "integer",
                            "description": "Maximum number of keys to return",
                            "default": 1000,
                        },
                    },
                    "required": ["bucket"],
                },
            },
            {
                "id": "aws.s3.get_object",
                "name": "AWS S3 Get Object",
                "description": "Get an object from S3",
                "category": "storage",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "bucket": {
                            "type": "string",
                            "description": "Name of the S3 bucket",
                        },
                        "key": {
                            "type": "string",
                            "description": "Key of the object to get",
                        },
                    },
                    "required": ["bucket", "key"],
                },
            },
            {
                "id": "aws.s3.put_object",
                "name": "AWS S3 Put Object",
                "description": "Put an object into S3",
                "category": "storage",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "bucket": {
                            "type": "string",
                            "description": "Name of the S3 bucket",
                        },
                        "key": {
                            "type": "string",
                            "description": "Key for the object",
                        },
                        "body": {
                            "type": "string",
                            "description": "Content of the object",
                        },
                        "content_type": {
                            "type": "string",
                            "description": "Content-Type header",
                        },
                    },
                    "required": ["bucket", "key", "body"],
                },
            },
            {
                "id": "aws.ec2.list_instances",
                "name": "AWS EC2 List Instances",
                "description": "List EC2 instances",
                "category": "compute",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filters": {
                            "type": "array",
                            "description": "Filters to apply",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "values": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results",
                            "default": 100,
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "aws.lambda.list_functions",
                "name": "AWS Lambda List Functions",
                "description": "List Lambda functions",
                "category": "serverless",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_items": {
                            "type": "integer",
                            "description": "Maximum number of functions to return",
                            "default": 100,
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "aws.lambda.invoke",
                "name": "AWS Lambda Invoke Function",
                "description": "Invoke a Lambda function",
                "category": "serverless",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "function_name": {
                            "type": "string",
                            "description": "Name of the Lambda function",
                        },
                        "payload": {
                            "type": "object",
                            "description": "Input payload for the function",
                        },
                    },
                    "required": ["function_name"],
                },
            },
            {
                "id": "aws.iam.list_users",
                "name": "AWS IAM List Users",
                "description": "List IAM users",
                "category": "security",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_items": {
                            "type": "integer",
                            "description": "Maximum number of users to return",
                            "default": 100,
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "aws.cloudformation.list_stacks",
                "name": "AWS CloudFormation List Stacks",
                "description": "List CloudFormation stacks",
                "category": "infrastructure",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "stack_status_filter": {
                            "type": "array",
                            "description": "Filter by stack status",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [],
                },
            },
        ]
    
    def get_config_schema(self) -> dict[str, Any] | None:
        """Get the configuration schema for this integration."""
        return {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "AWS region",
                    "default": "us-east-1",
                    "enum": [
                        "us-east-1", "us-east-2", "us-west-1", "us-west-2",
                        "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1",
                        "ap-northeast-1", "ap-northeast-2", "ap-northeast-3",
                        "ap-southeast-1", "ap-southeast-2", "ap-southeast-3",
                        "ap-south-1", "sa-east-1", "ca-central-1",
                    ],
                },
                "profile": {
                    "type": "string",
                    "description": "AWS profile name",
                    "default": "default",
                },
                "access_key_id": {
                    "type": "string",
                    "description": "AWS access key ID (use secrets for production)",
                },
                "secret_access_key": {
                    "type": "string",
                    "description": "AWS secret access key (use secrets for production)",
                },
                "session_token": {
                    "type": "string",
                    "description": "AWS session token for temporary credentials",
                },
                "default_service_config": {
                    "type": "object",
                    "description": "Default configuration for AWS services",
                    "properties": {
                        "retries": {
                            "type": "integer",
                            "description": "Number of retries",
                            "default": 3,
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds",
                            "default": 30,
                        },
                    },
                },
            },
            "required": [],
        }
    
    def validate_config(self, config: dict[str, Any]) -> tuple[bool, str]:
        """Validate configuration for this integration."""
        # Check region
        valid_regions = [
            "us-east-1", "us-east-2", "us-west-1", "us-west-2",
            "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1",
            "ap-northeast-1", "ap-northeast-2", "ap-northeast-3",
            "ap-southeast-1", "ap-southeast-2", "ap-southeast-3",
            "ap-south-1", "sa-east-1", "ca-central-1",
        ]
        
        region = config.get("region", "us-east-1")
        if region not in valid_regions:
            return (False, f"Invalid region: {region}. Must be one of {valid_regions}")
        
        # Check that at least one credential method is provided
        has_credentials = (
            config.get("access_key_id") or
            config.get("secret_access_key") or
            config.get("profile")
        )
        
        if not has_credentials:
            logger.warning("No AWS credentials configured. Will use default credential chain.")
        
        return (True, "")
