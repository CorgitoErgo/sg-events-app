"""Write the API's OpenAPI schema for the mobile app's generated types.

    uv run python -m app.export_openapi ../mobile/src/api/openapi.json
    (then in mobile/: npm run gen:api)
"""

import json
import sys
from pathlib import Path

from app.main import app

if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", "utf-8")
    print(f"wrote {target}")
