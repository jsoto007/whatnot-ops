"""Deterministic provider for tests and e2e (AI_PROVIDER=fake; refused in production).

Answers by simple keyword rules so flows can be exercised without a vendor:
  - classify_line: "won"/"sold"/"bought" → sale; "cancel"/"refund" → cancel;
    "declin"/"unpaid"/"didn't pay" → payment_failed; else chatter.
  - match_item: the candidate alias with the most words in the text, or none.
  - audit_show: a finding for every buyer whose lines mention cancel/refund/
    declined while they still have an open sale.
"""

import json
import re

from .base import AiError, Decision


class FakeProvider:
    name = "fake"

    def __init__(self, scripted: list[dict] | None = None):
        self.scripted = list(scripted or [])
        self.calls: list[dict] = []

    def decide(self, *, task: str, input: str, schema: dict) -> Decision:
        self.calls.append({"task": task, "input": input, "schema": schema})
        if self.scripted:
            nxt = self.scripted.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return Decision(
                fields=nxt["fields"], confidence=nxt.get("confidence", 0.9), provider=self.name
            )
        purpose = task.split(":", 1)[0].strip()
        text = input.lower()
        if purpose == "classify_line":
            if re.search(r"\b(won|sold|bought|purchased)\b", text):
                kind, conf = "sale", 0.85
            elif re.search(r"cancel|refund", text):
                kind, conf = "cancel", 0.9
            elif re.search(r"declin|unpaid|didn'?t pay", text):
                kind, conf = "payment_failed", 0.9
            else:
                kind, conf = "chatter", 0.95
            return Decision(
                fields={"kind": kind, "reasoning": "keyword match"},
                confidence=conf,
                provider=self.name,
            )
        if purpose == "match_item":
            try:
                payload = json.loads(input)
            except ValueError as e:
                raise AiError("fake: bad input") from e
            words = set(re.findall(r"[a-z0-9]+", (payload.get("item") or "").lower()))
            best, score = None, 0
            for c in payload.get("candidates", []):
                s = len(words & set(re.findall(r"[a-z0-9]+", c["label"].lower())))
                if s > score:
                    best, score = c["skuId"], s
            return Decision(
                fields={"skuId": best, "reasoning": "word overlap"},
                confidence=0.96 if best else 0.3,
                provider=self.name,
            )
        if purpose == "audit_show":
            payload = json.loads(input)
            open_by_buyer = {s["buyer"].lower(): s for s in payload.get("openSales", [])}
            findings = []
            for line in payload.get("lines", []):
                low = line["text"].lower()
                if re.search(r"cancel|refund|declin|unpaid|didn'?t pay", low):
                    for buyer, sale in open_by_buyer.items():
                        if buyer in low:
                            kind = (
                                "payment_failed"
                                if re.search(r"declin|unpaid|didn'?t pay", low)
                                else "cancel_sale"
                            )
                            findings.append(
                                {
                                    "type": kind,
                                    "saleId": sale["id"],
                                    "buyer": sale["buyer"],
                                    "seq": line["seq"],
                                    "summary": (
                                        f"line #{line['seq']} says {sale['buyer']}'s "
                                        "purchase fell through"
                                    ),
                                    "confidence": 0.95,
                                }
                            )
            return Decision(fields={"findings": findings}, confidence=0.9, provider=self.name)
        raise AiError(f"fake: unknown purpose {purpose!r}")
