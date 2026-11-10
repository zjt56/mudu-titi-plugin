"""Shared source-title grammar for extraction, references and validation.

Only explicit title words count. IMG identifies a relationship, never an image's
meaning; a bare image cannot prove that explanation/summary text exists.
"""

IMG = r'\[IMG [^\]\[\r\n]+\]'
EXPLANATION_LABEL = rf'(?:【解析】|\[解析\]|{IMG}\s*解析\s*{IMG})'
SUMMARY_LABEL = rf'(?:【文章大意】|\[文章大意\]|{IMG}\s*文章大意\s*{IMG})'
# Existing reference-only aliases do not add content authority: the importer
# still verifies the expanded text against the original question's source.
REFERENCE_EXPLANATION_LABEL = rf'(?:{EXPLANATION_LABEL}|【\s*(?:解析|答案|分析|详解|思路点拨)\s*】)'
REFERENCE_SUMMARY_LABEL = rf'(?:{SUMMARY_LABEL}|【\s*文章大意\s*】)'
REFERENCE_FIELD_LABEL = rf'(?:{REFERENCE_EXPLANATION_LABEL}|{REFERENCE_SUMMARY_LABEL})\s*[:：]?\s*'
