"""
Smallest possible visual interface for the Arabic Research Assistant.

This file contains NO pipeline logic, NO prompt text, and NO model-calling
code of its own. It only:
  1. Collects an Arabic question from the user.
  2. Calls the existing pipeline_runner.run_pipeline(), reusing the exact
     same model-calling functions and configuration that run_assistant.py's
     CLI already uses (imported from run_assistant.py, unchanged).
  3. Renders the same "stages" result dict the CLI already prints as text.

This is a UI-polish layer only -- layout, spacing, section labels, and
Streamlit chrome settings. No backend, retrieval, prompt, model-selection,
or validation logic lives here.

Run with: streamlit run app.py
"""

import streamlit as st

import run_assistant as backend
from pipeline_runner import run_pipeline, PipelineError
from model_client import ModelClientError

st.set_page_config(page_title="مساعد البحث العلمي العربي", page_icon="📚", layout="centered")

# In-memory usage counter per access code: {code: searches_used}. This is
# NOT a database -- it lives only in this running app's memory, so it
# persists across visitors while the app stays up, but resets to zero on
# every redeploy or restart. Good enough to stop one code from running up
# unlimited API cost; not a real accounting system.
_USAGE_COUNTS = {}


def check_access_code() -> bool:
    """
    Multiple shared access codes (instead of one password), each with its own
    search limit -- so different people/customers can be given different
    codes, and no single code can spend unlimited API money. The real codes
    are never written in this file -- they live in Streamlit's own "secrets"
    storage (ACCESS_CODES table below), same rule as the API key.

    secrets.toml shape:
        [ACCESS_CODES]
        SOME_CODE = 20
        ANOTHER_CODE = 5
    (each value is that code's total allowed searches)
    """
    if st.session_state.get("authenticated"):
        return True

    st.title("📚 مساعد البحث العلمي العربي")
    code = st.text_input("رمز الدخول", type="password")
    if st.button("دخول"):
        access_codes = st.secrets.get("ACCESS_CODES", {})
        if code in access_codes:
            st.session_state["authenticated"] = True
            st.session_state["access_code"] = code
            st.rerun()
        else:
            st.error("رمز الدخول غير صحيح.")
    return False


if not check_access_code():
    st.stop()


def remaining_searches() -> int:
    """How many searches are left on the currently logged-in code."""
    code = st.session_state["access_code"]
    limit = st.secrets.get("ACCESS_CODES", {}).get(code, 0)
    used = _USAGE_COUNTS.get(code, 0)
    return max(0, limit - used)


def record_search_used() -> None:
    code = st.session_state["access_code"]
    _USAGE_COUNTS[code] = _USAGE_COUNTS.get(code, 0) + 1

THEME_OPTIONS = {"تلقائي (حسب الجهاز)": "auto", "فاتح": "light", "داكن": "dark"}

theme_choice = st.selectbox("المظهر", options=list(THEME_OPTIONS.keys()), key="theme_choice")
theme_mode = THEME_OPTIONS[theme_choice]

# Background/text colors per mode. "auto" uses a CSS media query so it
# follows the visitor's own device setting -- no JS, no extra dependency.
_LIGHT_RULE = ".stApp { background-color: #ffffff !important; color: #111111 !important; }"
_DARK_RULE = ".stApp { background-color: #0e1117 !important; color: #f5f5f5 !important; }"

if theme_mode == "light":
    theme_css = _LIGHT_RULE
elif theme_mode == "dark":
    theme_css = _DARK_RULE
else:
    theme_css = (
        f"@media (prefers-color-scheme: light) {{ {_LIGHT_RULE} }}"
        f"@media (prefers-color-scheme: dark) {{ {_DARK_RULE} }}"
    )

st.markdown(f"<style>{theme_css}</style>", unsafe_allow_html=True)

# Minimal RTL + readability styling -- no CSS framework, no JS, no animations.
st.markdown(
    """
    <style>
    .stApp { direction: rtl; text-align: right; }
    .stTextArea textarea { direction: rtl; text-align: right; font-size: 1.05rem; padding: 0.9rem; }
    .stButton button { direction: rtl; }
    h1 { margin-bottom: 0.2rem; }
    .app-subtitle { color: var(--text-color-secondary, #666); line-height: 1.8; margin-bottom: 1.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📚 مساعد البحث العلمي العربي")
st.markdown(
    '<p class="app-subtitle">اكتب سؤالاً بحثياً أكاديمياً باللغة العربية. سيقوم النظام بالبحث عن دراسات '
    "حقيقية من OpenAlex، وتقييم مدى صلتها بالسؤال، ثم كتابة ملخص أدلة عربي "
    "موثّق بالمصادر الفعلية — دون اختلاق أي معلومات.</p>",
    unsafe_allow_html=True,
)

question = st.text_area(
    "سؤالك البحثي",
    height=130,
    placeholder="مثال: ما تأثير استخدام الذكاء الاصطناعي التوليدي على التحصيل الأكاديمي لدى طلبة الجامعات؟",
)

search_clicked = st.button("بحث", type="primary", use_container_width=True)
st.caption(f"عمليات البحث المتبقية على هذا الرمز: {remaining_searches()}")


def render_result(question: str, stages: dict) -> None:
    queries = stages["queries"]
    search_report = stages["search_report"]
    relevance_report = stages["relevance_report"]
    selected_papers = stages["selected_papers"]
    synthesis = stages["synthesis"]

    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for p in relevance_report["papers"]:
        counts[p["relevance"]] += 1

    st.divider()

    # --- الدراسات المسترجعة -------------------------------------------------
    st.header("الدراسات المسترجعة")
    unique_papers = search_report["unique_papers"]
    st.caption(
        f"تم العثور على {len(unique_papers)} دراسة فريدة، منها "
        f"{counts['HIGH']} عالية الصلة و {counts['MEDIUM']} متوسطة الصلة و {counts['LOW']} منخفضة الصلة."
    )
    classifications_by_id = {p["openalex_id"]: p for p in relevance_report["papers"]}
    for p in selected_papers:
        rel = classifications_by_id[p["id"]]["relevance"]
        st.markdown(f"- **[{rel}]** {p['title']}")

    with st.expander("تفاصيل الاستعلامات والاسترجاع"):
        for q in queries["english_queries"]:
            st.write(f"🇬🇧 {q}")
        for q in queries["arabic_queries"]:
            st.write(f"🇸🇦 {q}")
        st.write("---")
        for q, info in search_report["per_query"].items():
            if info["error"]:
                st.write(f"\"{q}\" ← خطأ: {info['error']}")
            else:
                st.write(f"\"{q}\" ← {info['result_count']} نتيجة")

    # Evidence strength warning -- never manufacture confidence that isn't there.
    if counts["HIGH"] == 0:
        st.warning(
            "⚠️ لا توجد أوراق عالية الصلة (HIGH). الأدلة أدناه مبنية فقط على تطابقات "
            "متوسطة القوة (MEDIUM) وينبغي التعامل معها كأدلة أولية غير حاسمة."
        )

    # --- نتائج الدراسات ------------------------------------------------------
    st.header("نتائج الدراسات")
    for item in synthesis["what_studies_found"]:
        st.markdown(f"- {item['claim']}")
        st.caption("المصادر: " + ", ".join(item["supporting_paper_ids"]))

    # --- مواضع الاختلاف -------------------------------------------------------
    if synthesis.get("where_studies_disagree"):
        st.header("مواضع الاختلاف")
        for item in synthesis["where_studies_disagree"]:
            st.markdown(f"- {item['issue']}")
            st.caption("المصادر: " + ", ".join(item["supporting_paper_ids"]))

    # --- ما لا يمكن استنتاجه --------------------------------------------------
    if synthesis.get("what_cannot_be_concluded"):
        st.header("ما لا يمكن استنتاجه")
        for item in synthesis["what_cannot_be_concluded"]:
            st.markdown(f"- {item}")

    # --- الخلاصة ---------------------------------------------------------------
    st.header("الخلاصة")
    st.caption("هذا تفسير للأدلة المعروضة أعلاه، وليس نتيجة منشورة في أي دراسة بمفردها.")
    st.write(synthesis["ai_synthesis"])

    # --- المصادر -----------------------------------------------------------
    # Exact metadata reconstructed deterministically by Python from the
    # original OpenAlex records -- the model never generated this.
    st.header("المصادر")
    for s in synthesis["sources"]:
        authors = ", ".join(s["authors"]) if isinstance(s["authors"], list) else s["authors"]
        if s["url"]:
            st.markdown(f"**[{s['title']}]({s['url']})**")
        else:
            st.markdown(f"**{s['title']}**")
        st.write(f"{authors} ({s['year']})")
        if s["doi"]:
            st.caption(f"DOI: {s['doi']}")
        st.divider()

    st.caption("ملاحظة: هذا ليس مراجعة أدبية شاملة.")

    if backend.TOKEN_USAGE_LOG:
        with st.expander("استخدام الرموز / Token usage"):
            total_in = total_out = 0
            for entry in backend.TOKEN_USAGE_LOG:
                st.write(f"{entry['stage']} ({entry['model']}): {entry['input_tokens']} in / {entry['output_tokens']} out")
                total_in += entry["input_tokens"]
                total_out += entry["output_tokens"]
            st.write(f"**المجموع:** {total_in} input / {total_out} output tokens")


STAGE_LABELS = {
    1: "توليد الاستعلامات",
    2: "البحث في OpenAlex",
    3: "تصنيف الصلة",
    4: "اختيار الدراسات",
    5: "استخلاص الأدلة",
    6: "التوليف النهائي",
    7: "التحقق من النتائج",
}

if search_clicked:
    if not question.strip():
        st.warning("الرجاء إدخال سؤال بحثي أولاً.")
    elif remaining_searches() <= 0:
        st.error("لقد استنفدت عدد عمليات البحث المسموح بها لهذا الرمز.")
    else:
        record_search_used()

        # Reset per-search so token usage doesn't accumulate across searches
        # in the same running app (Streamlit reruns the script, but the
        # imported run_assistant module -- and its state -- persists).
        backend.TOKEN_USAGE_LOG.clear()

        # user_message / technical_name are set on failure and rendered
        # AFTER the status block closes, so an error is never hidden inside
        # a collapsed status widget the user would have to re-expand.
        stages = None
        user_message = None
        technical_name = None

        with st.status("جارٍ تنفيذ البحث...", expanded=True) as status:
            def on_progress(step: int, total: int, message: str) -> None:
                label = STAGE_LABELS.get(step, message)
                status.write(f"[{step}/{total}] {label}")

            try:
                stages = run_pipeline(
                    question,
                    query_generator=backend.generate_queries,
                    relevance_classifier=backend.classify_relevance,
                    extractor=backend.extract_findings,
                    synthesizer=backend.synthesize_final,
                    progress=on_progress,
                )
            except ModelClientError as error:
                print(f"[server-only log] ModelClientError: {error}")  # console only, never shown in the browser
                status.update(label="تعذّر إتمام البحث", state="error", expanded=False)
                user_message = "تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً."
                technical_name = type(error).__name__
            except PipelineError as error:
                print(f"[server-only log] PipelineError: {error}")  # console only, never shown in the browser
                status.update(label="تعذّر إتمام البحث", state="error", expanded=False)
                user_message = "تعذّر إكمال معالجة النتائج. يرجى المحاولة مرة أخرى أو تعديل السؤال."
                technical_name = type(error).__name__
            except Exception as error:
                # Safety net: never show a raw traceback or internal details to the user.
                status.update(label="حدث خطأ غير متوقع", state="error", expanded=False)
                user_message = "حدث خطأ غير متوقع. لم يتم عرض أي نتيجة غير مكتملة."
                technical_name = type(error).__name__
            else:
                status.update(label="اكتمل البحث", state="complete", expanded=False)

        if user_message:
            st.error(user_message)
            with st.expander("تفاصيل تقنية"):
                st.caption(technical_name)
        elif stages is not None:
            render_result(question, stages)
