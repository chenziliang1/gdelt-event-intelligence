"""
Moved to backend/services/report_checks.py: the checks now also run on every live report
(ReportGenerator.generate). This alias keeps the eval scripts' `import answer_quality` working.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services import report_checks  # noqa: E402

sys.modules[__name__] = report_checks
