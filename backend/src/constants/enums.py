from enum import StrEnum
class Role(StrEnum):
    ADMIN="admin"
    ANALYST="analyst"
    VIEWER="viewer"
class RunStatus(StrEnum):
    QUEUED="queued"
    RUNNING="running"
    COMPLETED="completed"
    FAILED="failed"
class QualificationMode(StrEnum):
    RAW="raw"
    CALIBRATION="calibration_only"
class QualificationStatus(StrEnum):
    DRAFT="draft"
    APPROVED="approved"
class MergeSide(StrEnum):
    REFERENCE="reference"
    CANDIDATE="candidate"
class MergeEventKind(StrEnum):
    READING="reading"
    WITHDRAWAL="withdrawal"
class MergeEventState(StrEnum):
    STAGED="staged"
    APPLIED="applied"
    QUARANTINED="quarantined"
    DUPLICATE="duplicate"
class MergeSpecimenStatus(StrEnum):
    WAITING="waiting"
    PARTIAL="partial"
    ACTIVE="active"
    WITHDRAWN="withdrawn"
    BLOCKED="blocked"
