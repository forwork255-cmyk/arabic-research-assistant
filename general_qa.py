"""
General question answering: a separate, lightweight path for questions that
are NOT academic research questions (see moderation.format_question_classification_prompt,
which routes here). A normal helpful AI answer -- no OpenAlex search, no
per-paper extraction, no citations claimed -- clearly distinct from the
grounded/cited research pipeline in pipeline_runner.py.

Still bound by the project's core rule: never invent statistics, studies,
or citations and present them as verified academic fact. This mode is
allowed to be a normal helpful assistant, not allowed to fabricate
scholarship while doing so.
"""


def format_general_answer_prompt(question: str) -> str:
    return f"""أنت مساعد ذكاء اصطناعي مفيد تجيب باللغة العربية. هذا سؤال عام (وليس سؤالاً بحثياً أكاديمياً يتطلب البحث في دراسات حقيقية)، فأجب عليه مباشرة بأفضل ما لديك من معرفة.

قاعدة مهمة: لا تخترع أسماء دراسات أو أبحاث أو إحصائيات أو اقتباسات وتقدّمها كأنها حقائق علمية موثقة. إذا استشهدت بمعلومة عامة معروفة فلا بأس، لكن لا تنسب أرقاماً أو نتائج محددة إلى "دراسة" أو "بحث" لم يُذكر لك فعلياً.

السؤال:
\"\"\"{question}\"\"\"

أجب بشكل واضح ومباشر باللغة العربية."""
