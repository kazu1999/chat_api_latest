import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError


AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-1")
FAQ_TABLE_NAME = os.getenv("FAQ_TABLE_NAME", "ueki-faq")

_ddb_resource = None
_faq_table = None


def _get_table():
    global _ddb_resource, _faq_table
    if _faq_table is not None:
        return _faq_table
    try:
        _ddb_resource = boto3.resource("dynamodb", region_name=AWS_REGION)
        _faq_table = _ddb_resource.Table(FAQ_TABLE_NAME)
        return _faq_table
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_faq(question: str, answer: str) -> Dict[str, Any]:
    """Create a new FAQ item. Fails if the question already exists."""
    table = _get_table()
    if table is None:
        return {"ok": False, "error": "DynamoDB table not available"}
    item = {
        "question": question,
        "answer": answer,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(question)")
        return {"ok": True, "item": item}
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"ok": False, "error": "Question already exists"}
        return {"ok": False, "error": str(e)}
    except (BotoCoreError, Exception) as e:
        return {"ok": False, "error": str(e)}


def get_faq(question: str) -> Optional[Dict[str, Any]]:
    """Get an FAQ item by question. Returns None if not found."""
    table = _get_table()
    if table is None:
        return None
    try:
        resp = table.get_item(Key={"question": question})
        return resp.get("Item")
    except (BotoCoreError, ClientError):
        return None


def update_faq(question: str, answer: str) -> Dict[str, Any]:
    """Update the answer for an existing question."""
    table = _get_table()
    if table is None:
        return {"ok": False, "error": "DynamoDB table not available"}
    try:
        resp = table.update_item(
            Key={"question": question},
            UpdateExpression="SET answer = :a, updated_at = :u",
            ExpressionAttributeValues={
                ":a": answer,
                ":u": _now_iso(),
            },
            ConditionExpression="attribute_exists(question)",
            ReturnValues="ALL_NEW",
        )
        return {"ok": True, "item": resp.get("Attributes")}
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"ok": False, "error": "Question not found"}
        return {"ok": False, "error": str(e)}
    except (BotoCoreError, Exception) as e:
        return {"ok": False, "error": str(e)}


def delete_faq(question: str) -> Dict[str, Any]:
    """Delete an FAQ item by question."""
    table = _get_table()
    if table is None:
        return {"ok": False, "error": "DynamoDB table not available"}
    try:
        table.delete_item(
            Key={"question": question},
            ConditionExpression="attribute_exists(question)",
        )
        return {"ok": True}
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"ok": False, "error": "Question not found"}
        return {"ok": False, "error": str(e)}
    except (BotoCoreError, Exception) as e:
        return {"ok": False, "error": str(e)}


def list_faqs(limit: int = 20, last_evaluated_key: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Scan FAQs with pagination. Returns {'items': [...], 'last_evaluated_key': ...}.

    Note: For production, consider using a GSI for categories/tags or a search index.
    """
    table = _get_table()
    if table is None:
        return {"ok": False, "error": "DynamoDB table not available"}
    try:
        scan_kwargs: Dict[str, Any] = {"Limit": limit}
        if last_evaluated_key:
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key
        resp = table.scan(**scan_kwargs)
        return {
            "ok": True,
            "items": resp.get("Items", []),
            "last_evaluated_key": resp.get("LastEvaluatedKey"),
        }
    except (BotoCoreError, ClientError) as e:
        return {"ok": False, "error": str(e)}


__all__ = [
    "create_faq",
    "get_faq",
    "update_faq",
    "delete_faq",
    "list_faqs",
]


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="FAQ DynamoDB CRUD CLI")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    p_create = subparsers.add_parser("create", help="Create a new FAQ")
    p_create.add_argument("question", help="Question text (partition key)")
    p_create.add_argument("answer", help="Answer text")

    p_get = subparsers.add_parser("get", help="Get an FAQ by question")
    p_get.add_argument("question", help="Question text (partition key)")

    p_update = subparsers.add_parser("update", help="Update an existing FAQ's answer")
    p_update.add_argument("question", help="Question text (partition key)")
    p_update.add_argument("answer", help="New answer text")

    p_delete = subparsers.add_parser("delete", help="Delete an FAQ by question")
    p_delete.add_argument("question", help="Question text (partition key)")

    p_list = subparsers.add_parser("list", help="List FAQs (scan)")
    p_list.add_argument("--limit", type=int, default=20, help="Max items to return")

    args = parser.parse_args()

    if args.cmd == "create":
        res = create_faq(args.question, args.answer)
        print(_json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "get":
        item = get_faq(args.question)
        print(_json.dumps({"item": item}, ensure_ascii=False, indent=2))
    elif args.cmd == "update":
        res = update_faq(args.question, args.answer)
        print(_json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "delete":
        res = delete_faq(args.question)
        print(_json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "list":
        res = list_faqs(limit=args.limit)
        print(_json.dumps(res, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


