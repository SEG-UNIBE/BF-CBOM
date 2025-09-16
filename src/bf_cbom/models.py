"""Core models for BF-CBOM."""

from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum


class CryptoLibraryType(str, Enum):
    """Types of cryptographic libraries."""
    SYMMETRIC_ENCRYPTION = "symmetric_encryption"
    ASYMMETRIC_ENCRYPTION = "asymmetric_encryption"
    HASHING = "hashing"
    DIGITAL_SIGNATURE = "digital_signature"
    KEY_DERIVATION = "key_derivation"
    RANDOM_NUMBER_GENERATION = "random_number_generation"


class VulnerabilitySeverity(str, Enum):
    """Vulnerability severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CryptoLibrary(BaseModel):
    """Model for a cryptographic library."""
    name: str
    version: str
    library_type: CryptoLibraryType
    usage_frequency: str
    performance_metrics: Dict[str, float]
    security_assessment: Dict[str, Any]


class Vulnerability(BaseModel):
    """Model for a security vulnerability."""
    cve_id: Optional[str] = None
    severity: VulnerabilitySeverity
    description: str
    affected_library: str
    affected_versions: List[str]
    mitigation: Optional[str] = None


class Project(BaseModel):
    """Model for a software project being analyzed."""
    name: str
    url: str
    language: str
    framework: Optional[str] = None
    crypto_libraries: List[CryptoLibrary]
    vulnerabilities: List[Vulnerability]
    analysis_timestamp: datetime


class BenchmarkResult(BaseModel):
    """Model for benchmark results."""
    library_name: str
    operation: str
    throughput_ops_per_sec: float
    latency_ms: float
    memory_usage_mb: float
    cpu_usage_percent: float
    test_duration_sec: float