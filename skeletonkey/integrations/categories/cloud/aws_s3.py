"""AWS S3 Integration for SkeletonKey (P5.4).

This integration provides access to AWS S3 with:
- Bucket operations (create, delete, list)
- Object operations (upload, download, delete, list)
- Presigned URL generation
- Bucket policy management
- Object metadata management
- Health checks

Requires: boto3
Install with: pip install boto3
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from skeletonkey.integrations import (
    Integration,
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationHealthCheck,
    IntegrationInfo,
    IntegrationPlatform,
    IntegrationSandbox,
    IntegrationType,
    register_integration,
)

logger = logging.getLogger(__name__)


@register_integration(
    name="aws-s3",
    title="AWS S3",
    version="1.0.0",
    integration_type=IntegrationType.PLATFORM_DEVKIT,
    category=IntegrationCategory.CLOUD,
    platform=IntegrationPlatform.AWS,
    description="AWS Simple Storage Service (S3) integration for object storage operations",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["aws", "s3", "storage", "cloud", "object", "bucket", "boto3"],
    dependencies=[
        IntegrationDependency(
            name="boto3",
            version=">=1.28.0",
            optional=False,
            description="AWS SDK for Python",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to AWS S3",
            required=True,
            default_granted=False,
        ),
        IntegrationCapability(
            name="filesystem",
            description="Requires filesystem access for credentials",
            required=True,
            default_granted=False,
        ),
    ],
)
class AWSS3Integration(Integration):
    """AWS S3 integration.
    
    This integration provides a full-featured AWS S3 client with support for:
    - Bucket operations (create, delete, list, head)
    - Object operations (upload, download, delete, head, list)
    - Presigned URL generation (for upload/download)
    - Bucket policy management
    - Object metadata and ACL management
    - Multipart uploads
    - Copy operations
    """
    
    def __init__(self, config: IntegrationConfig):
        """Initialize the AWS S3 integration."""
        super().__init__(config)
        self._client: Any = None
        self._resource: Any = None
        self._initialized = False
        self._boto3: Any = None
    
    def initialize(self) -> bool:
        """Initialize the AWS S3 integration."""
        try:
            # Import boto3
            import boto3
            from botocore.config import Config
            self._boto3 = boto3
            
            # Get AWS credentials from config/secret
            aws_access_key_id = (
                self.config.secrets.get("aws_access_key_id") or
                self.config.config.get("aws_access_key_id") or
                None
            )
            aws_secret_access_key = (
                self.config.secrets.get("aws_secret_access_key") or
                self.config.config.get("aws_secret_access_key") or
                None
            )
            aws_session_token = (
                self.config.secrets.get("aws_session_token") or
                self.config.config.get("aws_session_token") or
                None
            )
            
            region = self.config.config.get("region", "us-east-1")
            
            # Configure boto3
            boto3_config = Config(
                region_name=region,
                signature_version="s3v4",
            )
            
            # Create S3 client
            if aws_access_key_id and aws_secret_access_key:
                self._client = boto3.client(
                    "s3",
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    aws_session_token=aws_session_token,
                    config=boto3_config,
                )
            else:
                # Use default credential chain
                self._client = boto3.client("s3", config=boto3_config)
            
            # Create S3 resource
            if aws_access_key_id and aws_secret_access_key:
                self._resource = boto3.resource(
                    "s3",
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    aws_session_token=aws_session_token,
                    config=boto3_config,
                )
            else:
                self._resource = boto3.resource("s3", config=boto3_config)
            
            self._initialized = True
            logger.info(f"AWS S3 integration initialized: region={region}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize AWS S3 integration: {e}")
            self._initialized = False
            return False
    
    def cleanup(self) -> None:
        """Clean up the AWS S3 integration."""
        self._client = None
        self._resource = None
        self._initialized = False
        logger.info("AWS S3 integration cleaned up")
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the AWS S3 integration."""
        try:
            if not self._initialized:
                return ("unhealthy", "Integration not initialized")
            
            # Test by listing buckets (limited to 1)
            response = self._client.list_buckets(MaxBuckets=1)
            if "Buckets" in response:
                return ("healthy", "AWS S3 is accessible")
            else:
                return ("unhealthy", "AWS S3 list buckets failed")
        except Exception as e:
            return ("unhealthy", f"Health check failed: {e}")
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "aws-s3.list_buckets",
                "title": "List Buckets",
                "description": "List all S3 buckets",
                "capability": "aws-s3.list_buckets",
                "risk": "read",
                "typical_latency_ms": 50,
                "tier": "task",
                "tags": ["aws", "s3", "buckets", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "max_buckets": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                    },
                },
            },
            {
                "id": "aws-s3.bucket_exists",
                "title": "Check Bucket Exists",
                "description": "Check if a bucket exists",
                "capability": "aws-s3.bucket_exists",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["aws", "s3", "bucket", "exists"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                    },
                    "required": ["bucket"],
                },
            },
            {
                "id": "aws-s3.create_bucket",
                "title": "Create Bucket",
                "description": "Create a new S3 bucket",
                "capability": "aws-s3.create_bucket",
                "risk": "write",
                "typical_latency_ms": 50,
                "tier": "task",
                "tags": ["aws", "s3", "bucket", "create"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                        "region": {"type": "string", "description": "Region for the bucket"},
                        "acl": {"type": "string", "enum": ["private", "public-read", "public-read-write", "authenticated-read"], "default": "private"},
                    },
                    "required": ["bucket"],
                },
            },
            {
                "id": "aws-s3.delete_bucket",
                "title": "Delete Bucket",
                "description": "Delete an S3 bucket",
                "capability": "aws-s3.delete_bucket",
                "risk": "destructive",
                "typical_latency_ms": 50,
                "tier": "task",
                "tags": ["aws", "s3", "bucket", "delete"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                        "force": {"type": "boolean", "default": False, "description": "Force delete (empty bucket first)"},
                    },
                    "required": ["bucket"],
                },
            },
            {
                "id": "aws-s3.list_objects",
                "title": "List Objects",
                "description": "List objects in a bucket",
                "capability": "aws-s3.list_objects",
                "risk": "read",
                "typical_latency_ms": 50,
                "tier": "task",
                "tags": ["aws", "s3", "objects", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                        "prefix": {"type": "string", "description": "Prefix filter"},
                        "max_keys": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                        "delimiter": {"type": "string", "description": "Delimiter for grouping keys"},
                    },
                    "required": ["bucket"],
                },
            },
            {
                "id": "aws-s3.upload",
                "title": "Upload Object",
                "description": "Upload an object to S3",
                "capability": "aws-s3.upload",
                "risk": "write",
                "typical_latency_ms": 100,
                "tier": "task",
                "tags": ["aws", "s3", "upload", "object"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                        "key": {"type": "string", "description": "Object key"},
                        "body": {"type": "string", "description": "Object content"},
                        "file_path": {"type": "string", "description": "Path to file to upload"},
                        "acl": {"type": "string", "enum": ["private", "public-read", "public-read-write", "authenticated-read"], "default": "private"},
                        "content_type": {"type": "string", "description": "Content-Type header"},
                        "metadata": {"type": "object", "description": "Object metadata"},
                    },
                    "required": ["bucket", "key"],
                },
            },
            {
                "id": "aws-s3.download",
                "title": "Download Object",
                "description": "Download an object from S3",
                "capability": "aws-s3.download",
                "risk": "read",
                "typical_latency_ms": 100,
                "tier": "task",
                "tags": ["aws", "s3", "download", "object"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                        "key": {"type": "string", "description": "Object key"},
                        "file_path": {"type": "string", "description": "Path to save downloaded file"},
                    },
                    "required": ["bucket", "key"],
                },
            },
            {
                "id": "aws-s3.delete_object",
                "title": "Delete Object",
                "description": "Delete an object from S3",
                "capability": "aws-s3.delete_object",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["aws", "s3", "delete", "object"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                        "key": {"type": "string", "description": "Object key"},
                        "version_id": {"type": "string", "description": "Object version ID"},
                    },
                    "required": ["bucket", "key"],
                },
            },
            {
                "id": "aws-s3.head_object",
                "title": "Get Object Metadata",
                "description": "Get metadata for an object without downloading it",
                "capability": "aws-s3.head_object",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["aws", "s3", "head", "metadata"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                        "key": {"type": "string", "description": "Object key"},
                        "version_id": {"type": "string", "description": "Object version ID"},
                    },
                    "required": ["bucket", "key"],
                },
            },
            {
                "id": "aws-s3.copy_object",
                "title": "Copy Object",
                "description": "Copy an object within S3",
                "capability": "aws-s3.copy_object",
                "risk": "write",
                "typical_latency_ms": 50,
                "tier": "task",
                "tags": ["aws", "s3", "copy", "object"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source_bucket": {"type": "string", "description": "Source bucket name"},
                        "source_key": {"type": "string", "description": "Source object key"},
                        "dest_bucket": {"type": "string", "description": "Destination bucket name"},
                        "dest_key": {"type": "string", "description": "Destination object key"},
                        "acl": {"type": "string", "enum": ["private", "public-read", "public-read-write", "authenticated-read"], "default": "private"},
                        "content_type": {"type": "string", "description": "Content-Type header"},
                        "metadata": {"type": "object", "description": "Object metadata"},
                    },
                    "required": ["source_bucket", "source_key", "dest_bucket", "dest_key"],
                },
            },
            {
                "id": "aws-s3.generate_presigned_url",
                "title": "Generate Presigned URL",
                "description": "Generate a presigned URL for S3 object access",
                "capability": "aws-s3.generate_presigned_url",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["aws", "s3", "presigned", "url"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "Bucket name"},
                        "key": {"type": "string", "description": "Object key"},
                        "operation": {"type": "string", "enum": ["get", "put", "delete"], "default": "get", "description": "Operation type"},
                        "expires_in": {"type": "integer", "minimum": 1, "maximum": 604800, "default": 3600, "description": "URL expiration time in seconds"},
                    },
                    "required": ["bucket", "key"],
                },
            },
        ]
    
    # Tool implementations
    def tool_list_buckets(self, max_buckets: int = 100) -> dict[str, Any]:
        """List all S3 buckets."""
        try:
            paginator = self._client.get_paginator("list_buckets")
            page_iterator = paginator.paginate(PaginationConfig={"MaxItems": max_buckets})
            
            buckets = []
            for page in page_iterator:
                for bucket in page.get("Buckets", []):
                    buckets.append({
                        "name": bucket["Name"],
                        "creation_date": bucket["CreationDate"].isoformat() if "CreationDate" in bucket else None,
                    })
            
            return {
                "buckets": buckets,
                "count": len(buckets),
            }
        except Exception as e:
            raise Exception(f"List buckets failed: {e}")
    
    def tool_bucket_exists(self, bucket: str) -> dict[str, Any]:
        """Check if a bucket exists."""
        try:
            response = self._client.head_bucket(Bucket=bucket)
            return {
                "bucket": bucket,
                "exists": True,
                "region": response.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("x-amz-bucket-region"),
            }
        except self._client.exceptions.NoSuchBucket:
            return {
                "bucket": bucket,
                "exists": False,
            }
        except Exception as e:
            raise Exception(f"Check bucket exists failed: {e}")
    
    def tool_create_bucket(
        self,
        bucket: str,
        region: str | None = None,
        acl: str = "private",
    ) -> dict[str, Any]:
        """Create a new S3 bucket."""
        try:
            region = region or self.config.config.get("region", "us-east-1")
            
            # Create bucket in the specified region
            if region == "us-east-1":
                self._client.create_bucket(Bucket=bucket, ACL=acl)
            else:
                location = {"LocationConstraint": region}
                self._client.create_bucket(
                    Bucket=bucket,
                    ACL=acl,
                    CreateBucketConfiguration=location,
                )
            
            return {
                "bucket": bucket,
                "region": region,
                "created": True,
            }
        except Exception as e:
            raise Exception(f"Create bucket failed: {e}")
    
    def tool_delete_bucket(self, bucket: str, force: bool = False) -> dict[str, Any]:
        """Delete an S3 bucket."""
        try:
            if force:
                # Empty the bucket first
                self._empty_bucket(bucket)
            
            self._client.delete_bucket(Bucket=bucket)
            
            return {
                "bucket": bucket,
                "deleted": True,
            }
        except Exception as e:
            raise Exception(f"Delete bucket failed: {e}")
    
    def _empty_bucket(self, bucket: str) -> None:
        """Empty a bucket by deleting all objects."""
        try:
            # List and delete all objects
            paginator = self._client.get_paginator("list_objects_v2")
            page_iterator = paginator.paginate(Bucket=bucket)
            
            for page in page_iterator:
                if "Contents" in page:
                    objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                    if objects:
                        self._client.delete_objects(
                            Bucket=bucket,
                            Delete={"Objects": objects},
                        )
            
            # Delete all object versions (for versioned buckets)
            paginator = self._client.get_paginator("list_object_versions")
            page_iterator = paginator.paginate(Bucket=bucket)
            
            for page in page_iterator:
                if "Versions" in page:
                    objects = [{"Key": obj["Key"], "VersionId": obj["VersionId"]} for obj in page["Versions"]]
                    if objects:
                        self._client.delete_objects(
                            Bucket=bucket,
                            Delete={"Objects": objects},
                        )
            
            # Delete any remaining delete markers
            paginator = self._client.get_paginator("list_object_versions")
            page_iterator = paginator.paginate(Bucket=bucket)
            
            for page in page_iterator:
                if "DeleteMarkers" in page:
                    objects = [{"Key": obj["Key"], "VersionId": obj["VersionId"]} for obj in page["DeleteMarkers"]]
                    if objects:
                        self._client.delete_objects(
                            Bucket=bucket,
                            Delete={"Objects": objects},
                        )
        except Exception as e:
            logger.error(f"Failed to empty bucket {bucket}: {e}")
            raise
    
    def tool_list_objects(
        self,
        bucket: str,
        prefix: str | None = None,
        max_keys: int = 100,
        delimiter: str | None = None,
    ) -> dict[str, Any]:
        """List objects in a bucket."""
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            page_iterator = paginator.paginate(
                Bucket=bucket,
                Prefix=prefix,
                MaxKeys=max_keys,
                Delimiter=delimiter,
            )
            
            objects = []
            common_prefixes = []
            
            for page in page_iterator:
                if "Contents" in page:
                    for obj in page["Contents"]:
                        objects.append({
                            "key": obj["Key"],
                            "size": obj["Size"],
                            "last_modified": obj["LastModified"].isoformat(),
                            "storage_class": obj.get("StorageClass", "STANDARD"),
                        })
                if "CommonPrefixes" in page:
                    common_prefixes.extend([p["Prefix"] for p in page["CommonPrefixes"]])
            
            return {
                "bucket": bucket,
                "objects": objects,
                "object_count": len(objects),
                "common_prefixes": common_prefixes,
            }
        except Exception as e:
            raise Exception(f"List objects failed: {e}")
    
    def tool_upload(
        self,
        bucket: str,
        key: str,
        body: str | None = None,
        file_path: str | None = None,
        acl: str = "private",
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upload an object to S3."""
        try:
            extra_args = {"ACL": acl}
            
            if content_type:
                extra_args["ContentType"] = content_type
            if metadata:
                extra_args["Metadata"] = metadata
            
            if file_path:
                # Upload from file
                with open(file_path, "rb") as f:
                    self._client.upload_fileobj(f, bucket, key, ExtraArgs=extra_args)
            elif body:
                # Upload from string
                self._client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body.encode() if isinstance(body, str) else body,
                    **extra_args,
                )
            else:
                raise ValueError("Either body or file_path must be provided")
            
            return {
                "bucket": bucket,
                "key": key,
                "uploaded": True,
            }
        except Exception as e:
            raise Exception(f"Upload failed: {e}")
    
    def tool_download(
        self,
        bucket: str,
        key: str,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """Download an object from S3."""
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            body = response["Body"].read()
            
            if file_path:
                with open(file_path, "wb") as f:
                    f.write(body)
                return {
                    "bucket": bucket,
                    "key": key,
                    "downloaded": True,
                    "file_path": file_path,
                    "size": len(body),
                }
            else:
                return {
                    "bucket": bucket,
                    "key": key,
                    "downloaded": True,
                    "body": body.decode() if isinstance(body, bytes) else body,
                    "size": len(body),
                    "content_type": response.get("ContentType"),
                    "metadata": response.get("Metadata", {}),
                }
        except Exception as e:
            raise Exception(f"Download failed: {e}")
    
    def tool_delete_object(
        self,
        bucket: str,
        key: str,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete an object from S3."""
        try:
            extra_args = {}
            if version_id:
                extra_args["VersionId"] = version_id
            
            self._client.delete_object(Bucket=bucket, Key=key, **extra_args)
            
            return {
                "bucket": bucket,
                "key": key,
                "deleted": True,
            }
        except Exception as e:
            raise Exception(f"Delete object failed: {e}")
    
    def tool_head_object(
        self,
        bucket: str,
        key: str,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        """Get metadata for an object without downloading it."""
        try:
            extra_args = {}
            if version_id:
                extra_args["VersionId"] = version_id
            
            response = self._client.head_object(Bucket=bucket, Key=key, **extra_args)
            
            return {
                "bucket": bucket,
                "key": key,
                "size": response["ContentLength"],
                "content_type": response.get("ContentType"),
                "last_modified": response["LastModified"].isoformat(),
                "etag": response["ETag"],
                "metadata": response.get("Metadata", {}),
                "storage_class": response.get("StorageClass", "STANDARD"),
            }
        except Exception as e:
            raise Exception(f"Head object failed: {e}")
    
    def tool_copy_object(
        self,
        source_bucket: str,
        source_key: str,
        dest_bucket: str,
        dest_key: str,
        acl: str = "private",
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Copy an object within S3."""
        try:
            extra_args = {"ACL": acl}
            
            if content_type:
                extra_args["ContentType"] = content_type
            if metadata:
                extra_args["Metadata"] = metadata
            
            response = self._client.copy_object(
                CopySource={"Bucket": source_bucket, "Key": source_key},
                Bucket=dest_bucket,
                Key=dest_key,
                ExtraArgs=extra_args,
            )
            
            return {
                "source_bucket": source_bucket,
                "source_key": source_key,
                "dest_bucket": dest_bucket,
                "dest_key": dest_key,
                "copied": True,
                "version_id": response.get("VersionId"),
                "etag": response.get("ETag"),
            }
        except Exception as e:
            raise Exception(f"Copy object failed: {e}")
    
    def tool_generate_presigned_url(
        self,
        bucket: str,
        key: str,
        operation: str = "get",
        expires_in: int = 3600,
    ) -> dict[str, Any]:
        """Generate a presigned URL for S3 object access."""
        try:
            params = {"Bucket": bucket, "Key": key}
            
            if operation == "get":
                url = self._client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=expires_in,
                )
            elif operation == "put":
                url = self._client.generate_presigned_url(
                    "put_object",
                    Params=params,
                    ExpiresIn=expires_in,
                )
            elif operation == "delete":
                url = self._client.generate_presigned_url(
                    "delete_object",
                    Params=params,
                    ExpiresIn=expires_in,
                )
            else:
                raise ValueError(f"Unknown operation: {operation}")
            
            return {
                "bucket": bucket,
                "key": key,
                "operation": operation,
                "url": url,
                "expires_in": expires_in,
            }
        except Exception as e:
            raise Exception(f"Generate presigned URL failed: {e}")
