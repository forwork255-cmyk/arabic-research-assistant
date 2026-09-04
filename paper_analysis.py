"""
Single-paper analysis: the user uploads one PDF and the model reads the
ACTUAL document (via Claude's native PDF support in model_client.py's
call_model_with_document) -- not a text extraction we did ourselves, and
never anything invented beyond what the paper actually contains.

Separate from pipeline_runner.py's multi-paper OpenAlex search flow: this
answers questions about (or summarizes) exactly one paper the user
provides, rather than searching for and comparing several papers.
"""

# Real per-token cost scales with file size (more pages = more input
# tokens), so this is deliberately well under Anthropic's 32 MB request
# limit -- a cost/abuse guard, not an API restriction.
MAX_PDF_BYTES = 15 * 1024 * 1024  # 15 MB


def format_paper_analysis_prompt(question: str | None) -> str:
    if question:
        return f"""أنت مساعد بحث أكاديمي. المستخدم أرفق ورقة بحثية علمية وطرح السؤال التالي عنها:

"{question}"

أجب بالاعتماد فقط على محتوى الورقة المرفقة. إن لم تحتوِ الورقة على إجابة واضحة لهذا السؤال، قل ذلك صراحةً بدلاً من التخمين أو استخدام معلومات من خارج الورقة. اكتب الإجابة باللغة العربية."""
    return """أنت مساعد بحث أكاديمي. المستخدم أرفق ورقة بحثية علمية دون سؤال محدد. لخّص الورقة باللغة العربية ضمن الأقسام التالية فقط:

- الهدف / السؤال البحثي
- المنهجية
- النتائج الرئيسية
- القيود التي ذكرها الباحثون (إن وجدت)

اعتمد فقط على ما ورد فعلياً في الورقة المرفقة. لا تخترع أي تفاصيل أو نتائج غير موجودة فيها."""


def format_paper_followup_prompt(previous_analysis: str, question: str) -> str:
    """A follow-up question about the same attached paper. The PDF is
    re-sent alongside this prompt (see model_client.call_model_with_document)
    so the model can ground the new answer in the actual document again,
    not just in its own previous summary."""
    return f"""أنت مساعد بحث أكاديمي. سبق أن قدمت للمستخدم هذا التحليل لورقة بحثية مرفقة:

{previous_analysis}

الآن يطرح المستخدم سؤالاً إضافياً عن نفس الورقة:

"{question}"

أجب بالاعتماد فقط على محتوى الورقة المرفقة. إن لم تحتوِ الورقة على إجابة واضحة لهذا السؤال، قل ذلك صراحةً بدلاً من التخمين أو استخدام معلومات من خارج الورقة. اكتب الإجابة باللغة العربية."""
