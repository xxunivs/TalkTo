from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RecordingAnalysisCommand:
    job_id: UUID
    recording_id: UUID
    subject_id: UUID


async def run_recording_analysis(
    _command: RecordingAnalysisCommand,
) -> None:
    """Future staged recording-analysis worker entry point."""
    raise NotImplementedError("Recording analysis worker is not implemented yet.")
