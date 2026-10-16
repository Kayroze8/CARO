import os
import json
from pydantic import ValidationError
# from schedulers.utils.s3_utils import get_json_from_s3
from schema import Caro


def validate_company_json(company_json: dict) -> bool:
    try:
        Caro.model_validate(company_json)
        return True
    except ValidationError as e:
        print(e)
        return False


if __name__ == "__main__":
    # cin = "U74899DL1994PLC061080"

    # company_json = get_json_from_s3(
    #     bucket_name=os.getenv("AWS_BUCKET"),
    #     variable_folder=cin,
    #     json_file_name=f"{cin}.json"
    # )
    # Resolve the input beside this script so execution works from any directory.
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "response_1790580250915.json")
    with open(json_path, "r", encoding="utf-8") as file:
        contents = file.read()

    if not contents.strip():
        raise ValueError(f"JSON file is empty: {json_path}")

    company_json = json.loads(contents)

    # print(company_json)
    is_valid = validate_company_json(company_json)

    print("Valid" if is_valid else "Invalid")