# chat_corpus.jsonl

Labelled chat lines for the rules in `app/services/rules.py`. **Synthetic**:
written from Whatnot's visible conventions ("X won the auction", "sold to",
giveaways) and the team's sheet vocabulary (Red, Bundle, Ultra, Mega,
No-Pick, Gift). It is a regression harness for the rules, not proof they
match real shows. Replace / extend it with lines from captured shows
(Phase 1 follow-up) and re-run `tests/test_rules.py`, which prints
precision and recall per kind.

One JSON object per line: `{"u": username, "b": body, "kind": expected,
"buyer"?, "item"?, "qty"?, "price"?, "type"?}`. `u` empty = platform line.
