import json
import runpy
from pathlib import Path

import pytest

synthetic_completion = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "smoke_pdf.py")
)["synthetic_completion"]


def test_pdf_smoke_returns_native_batch_array_with_preserved_ids_and_values():
    prompt = "## Here is the input:\n" + json.dumps(
        [
            {"id": 17, "input": "The maximum operating pressure must not exceed 16 bar."},
            {"id": 23, "input": "Technical brief / Device HM-200 / Revision 2.1"},
        ]
    )
    content, kind = synthetic_completion({"messages": [{"content": prompt}]})
    values = json.loads(content)
    assert kind == "batch"
    assert [value["id"] for value in values] == [17, 23]
    assert "не повинен перевищувати 16" in values[0]["output"]
    assert "HM-200" in values[1]["output"] and "2.1" in values[1]["output"]


def test_pdf_smoke_single_response_preserves_negation_and_rejects_unknown_documents():
    content, kind = synthetic_completion(
        {
            "messages": [
                {
                    "content": "Now translate the following text:\nDo not disconnect the sensor while the system is pressurized."
                }
            ]
        }
    )
    assert kind == "single" and content.startswith("Не від’єднуйте")
    with pytest.raises(ValueError, match="fixture"):
        synthetic_completion(
            {"messages": [{"content": "Now translate the following text:\nUnrelated document"}]}
        )
