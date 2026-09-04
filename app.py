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
import auth
import history
import global_limit
import moderation
from pipeline_runner import run_pipeline, expand_selection, answer_followup, research_followup, PipelineError
from model_client import ModelClientError

st.set_page_config(page_title="مساعد البحث العلمي العربي", page_icon="📚", layout="centered")


def show_login_and_signup() -> bool:
    """Email/password sign-up and login, backed by Firestore (see auth.py)."""
    if st.session_state.get("authenticated"):
        return True

    st.title("📚 مساعد البحث العلمي العربي")
    login_tab, signup_tab = st.tabs(["تسجيل الدخول", "إنشاء حساب جديد"])

    with login_tab:
        email = st.text_input("البريد الإلكتروني", key="login_email")
        password = st.text_input("كلمة المرور", type="password", key="login_password")
        if st.button("دخول"):
            if auth.verify_login(email, password):
                st.session_state["authenticated"] = True
                st.session_state["user_email"] = email.strip().lower()
                st.rerun()
            else:
                st.error("البريد الإلكتروني أو كلمة المرور غير صحيحة.")

    with signup_tab:
        new_email = st.text_input("البريد الإلكتروني", key="signup_email")
        new_password = st.text_input("كلمة المرور (8 أحرف على الأقل)", type="password", key="signup_password")
        if st.button("إنشاء حساب"):
            try:
                auth.create_account(new_email, new_password)
                st.session_state["authenticated"] = True
                st.session_state["user_email"] = new_email.strip().lower()
                st.rerun()
            except auth.AuthError as e:
                st.error(str(e))

    return False


if not show_login_and_signup():
    st.stop()

# Search history: a list of {"question": str, "stages": dict}, loaded once
# per browser session from Firestore (history.py) so it follows the logged-in
# account across devices/sessions. New searches are appended here AND saved
# to the database (see the completion handler below); interactive follow-ups
# on an existing entry (expand/Q&A) only update this in-memory copy, not the
# database -- reopening that entry after a fresh login shows the original
# result, not those follow-ups.
if "search_history" not in st.session_state:
    st.session_state["search_history"] = list(reversed(history.get_history(st.session_state["user_email"])))
st.session_state.setdefault("viewing_index", None)

with st.sidebar:
    st.subheader("سجل البحث")
    if st.button("+ بحث جديد", use_container_width=True):
        st.session_state["viewing_index"] = None
        st.rerun()
    st.divider()
    if not st.session_state["search_history"]:
        st.caption("لا يوجد سجل بعد في هذه الجلسة.")
    else:
        for i in range(len(st.session_state["search_history"]) - 1, -1, -1):
            entry = st.session_state["search_history"][i]
            label = entry["question"][:40] + ("…" if len(entry["question"]) > 40 else "")
            if st.button(label, key=f"history_{i}", use_container_width=True):
                st.session_state["viewing_index"] = i
                st.rerun()
    st.caption("السجل محفوظ في حسابك، ويظهر عند تسجيل الدخول لاحقاً.")


def remaining_searches() -> int:
    """
    How many searches are left on the currently logged-in account -- 0 if the
    site-wide emergency cap (global_limit.py) has been reached, regardless of
    this account's own remaining allowance. That cap protects the real API
    budget from being drained by many accounts/sign-ups at once, not just one.
    """
    if global_limit.global_limit_reached():
        return 0
    account = auth.get_account(st.session_state["user_email"])
    if account is None:
        return 0
    return max(0, account["search_limit"] - account["used"])


def record_search_used() -> None:
    auth.increment_used(st.session_state["user_email"])
    global_limit.increment_global_used()


def no_searches_left_message() -> str:
    if global_limit.global_limit_reached():
        return "بلغ التطبيق الحد الأقصى المؤقت لعدد عمليات البحث. يرجى المحاولة لاحقاً."
    return "لقد استنفدت عدد عمليات البحث المسموح بها لحسابك."


def is_question_appropriate(question: str) -> tuple:
    """
    Safety check run BEFORE the real pipeline/follow-up call and before it
    counts against the search limit (see moderation.py). Fails OPEN (allows
    the question through) if the moderation call itself errors or returns a
    malformed result -- a legitimate user should not be blocked by an
    infrastructure hiccup; the per-account and site-wide search caps remain
    the primary defense against cost abuse.
    """
    try:
        result = backend.check_question_moderation(moderation.format_moderation_prompt(question))
    except ModelClientError as error:
        print(f"[server-only log] Moderation check failed, allowing through: {error}")
        return True, None
    if not moderation.validate_moderation_output(result):
        print(f"[server-only log] Moderation returned malformed output, allowing through: {result}")
        return True, None
    if result["appropriate"]:
        return True, None
    return False, result["reason"]


# Light/Dark/"Use system setting" is handled by Streamlit's own built-in
# Settings menu (the app's "⋮" menu, top right) -- no custom picker or CSS
# needed for that; see .streamlit/config.toml's toolbarMode="viewer", which
# is what makes that Settings entry visible.

# Minimal RTL + readability styling -- no CSS framework, no JS, no animations.
st.markdown(
    """
    <style>
    .stApp { direction: rtl; text-align: right; }
    .stTextArea textarea { direction: rtl; text-align: right; font-size: 1.05rem; padding: 0.9rem; }
    .stButton button { direction: rtl; }
    h1 { margin-bottom: 0.2rem; }
    .app-subtitle { color: var(--text-color-secondary, #666); line-height: 1.8; margin-bottom: 1.6rem; }
    /* The RTL direction above breaks the sidebar's own width calculation
       (it collapses to a sliver, wrapping Arabic text one letter per line)
       unless a fixed width is forced here. */
    section[data-testid="stSidebar"] { min-width: 300px !important; width: 300px !important; }
    section[data-testid="stSidebar"] > div { width: 300px !important; direction: rtl; text-align: right; }
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


def short_id(openalex_id: str) -> str:
    return openalex_id.rstrip("/").rsplit("/", 1)[-1]


def format_source_links(paper_ids: list, sources: list) -> str:
    """Turn raw OpenAlex IDs (e.g. 'W123...') into clickable source-title
    links using the exact same Python-built bibliography shown in the
    المصادر section -- never anything the model generated."""
    sources_by_short_id = {short_id(s["openalex_id"]): s for s in sources}
    links = []
    for pid in paper_ids:
        source = sources_by_short_id.get(pid)
        if source and source.get("url"):
            links.append(f"[{source['title']}]({source['url']})")
        elif source:
            links.append(source["title"])
        else:
            links.append(pid)
    return "، ".join(links)


def build_report_text(question: str, stages: dict) -> str:
    """
    Plain-text version of the same result shown on screen, for the download
    button -- so a user can paste it into their own document. Built purely
    from already-validated pipeline output (same data render_result() shows),
    nothing new is generated here.
    """
    synthesis = stages["synthesis"]
    lines = [
        "السؤال البحثي",
        question,
        "",
        "ما وجدته الدراسات",
    ]
    for item in synthesis["what_studies_found"]:
        lines.append(f"- {item['claim']}")
        lines.append(f"  (المصادر: {', '.join(item['supporting_paper_ids'])})")

    if synthesis.get("where_studies_disagree"):
        lines += ["", "مواضع الاختلاف"]
        for item in synthesis["where_studies_disagree"]:
            lines.append(f"- {item['issue']}")
            lines.append(f"  (المصادر: {', '.join(item['supporting_paper_ids'])})")

    if synthesis.get("what_cannot_be_concluded"):
        lines += ["", "ما لا يمكن استنتاجه"]
        for item in synthesis["what_cannot_be_concluded"]:
            lines.append(f"- {item}")

    lines += ["", "الخلاصة (تفسير الذكاء الاصطناعي، وليس نتيجة منشورة)", synthesis["ai_synthesis"]]

    lines += ["", "المصادر"]
    for s in synthesis["sources"]:
        authors = ", ".join(s["authors"]) if isinstance(s["authors"], list) else s["authors"]
        lines.append(f"- {s['title']}")
        lines.append(f"  {authors} ({s['year']})")
        if s["doi"]:
            lines.append(f"  DOI: {s['doi']}")
        if s["url"]:
            lines.append(f"  {s['url']}")

    lines += ["", "ملاحظة: هذا ليس مراجعة أدبية شاملة."]
    return "\n".join(lines)


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

    def links_for(paper_ids: list) -> str:
        return format_source_links(paper_ids, synthesis["sources"])

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
        st.caption("المصادر: " + links_for(item["supporting_paper_ids"]))

    # --- مواضع الاختلاف -------------------------------------------------------
    if synthesis.get("where_studies_disagree"):
        st.header("مواضع الاختلاف")
        for item in synthesis["where_studies_disagree"]:
            st.markdown(f"- {item['issue']}")
            st.caption("المصادر: " + links_for(item["supporting_paper_ids"]))

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

    st.download_button(
        "تنزيل التقرير",
        data=build_report_text(question, stages),
        file_name="research_report.txt",
        mime="text/plain",
    )

    if backend.TOKEN_USAGE_LOG:
        with st.expander("استخدام الرموز / Token usage"):
            total_in = total_out = 0
            for entry in backend.TOKEN_USAGE_LOG:
                st.write(f"{entry['stage']} ({entry['model']}): {entry['input_tokens']} in / {entry['output_tokens']} out")
                total_in += entry["input_tokens"]
                total_out += entry["output_tokens"]
            st.write(f"**المجموع:** {total_in} input / {total_out} output tokens")


def _persist_entry(idx: int) -> None:
    """Save this entry's current stages/followups back to Firestore -- called
    after every expand/follow-up/research-escalation so re-opening this
    conversation later (even after logging out) shows the full thread, not
    just the original result."""
    entry = st.session_state["search_history"][idx]
    if entry.get("id"):
        history.update_entry(
            st.session_state["user_email"], entry["id"],
            entry["stages"], entry.get("followups", []),
        )


def render_expand_button(idx: int) -> None:
    """'+ أضف المزيد من الدراسات': re-runs synthesis over additional
    already-retrieved papers. Costs real API money (same remaining-searches
    accounting as a full search) but is much cheaper, since retrieval and
    relevance classification are never re-run."""
    entry = st.session_state["search_history"][idx]
    if remaining_searches() <= 0:
        st.caption(no_searches_left_message())
    elif st.button("+ أضف المزيد من الدراسات", key=f"expand_{idx}"):
        record_search_used()
        backend.TOKEN_USAGE_LOG.clear()
        try:
            with st.spinner("جارٍ إضافة المزيد من الدراسات..."):
                new_stages = expand_selection(
                    entry["question"], entry["stages"],
                    backend.extract_findings, backend.synthesize_final,
                )
        except PipelineError as error:
            print(f"[server-only log] PipelineError (expand): {error}")
            st.error("تعذّر إضافة المزيد من الدراسات. قد لا توجد دراسات إضافية متاحة.")
        except ModelClientError as error:
            print(f"[server-only log] ModelClientError (expand): {error}")
            st.error("تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً.")
        else:
            st.session_state["search_history"][idx]["stages"] = new_stages
            _persist_entry(idx)
            st.rerun()


def render_followup_thread(idx: int) -> None:
    """Renders past follow-up Q&A as chat bubbles, and offers the opt-in
    real-research escalation when the cheap answer says it wasn't enough."""
    entry = st.session_state["search_history"][idx]
    for i, fu in enumerate(entry.get("followups", [])):
        st.chat_message("user").write(fu["question"])
        with st.chat_message("assistant"):
            st.write(fu["answer"])
            all_sources = entry["stages"]["synthesis"]["sources"] + fu.get("new_sources", [])
            if fu["supporting_paper_ids"]:
                st.caption("المصادر: " + format_source_links(fu["supporting_paper_ids"], all_sources))

            if fu.get("sufficient") is False and not fu.get("researched"):
                st.caption("لم تكن النتائج الحالية كافية للإجابة على هذا السؤال.")
                if remaining_searches() <= 0:
                    st.caption(no_searches_left_message())
                elif st.button("ابحث عن دراسات جديدة لهذا السؤال (تكلفة إضافية)", key=f"research_followup_{idx}_{i}"):
                    record_search_used()
                    backend.TOKEN_USAGE_LOG.clear()
                    try:
                        with st.spinner("جارٍ البحث عن دراسات جديدة..."):
                            new_result = research_followup(
                                entry["question"], entry["stages"], fu["question"],
                                backend.generate_queries, backend.classify_relevance,
                                backend.extract_findings, backend.answer_followup_question,
                            )
                    except PipelineError as error:
                        print(f"[server-only log] PipelineError (research_followup): {error}")
                        st.error("تعذّر العثور على دراسات جديدة مناسبة لهذا السؤال.")
                    except ModelClientError as error:
                        print(f"[server-only log] ModelClientError (research_followup): {error}")
                        st.error("تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً.")
                    else:
                        new_result["researched"] = True
                        entry["followups"][i] = {"question": fu["question"], **new_result}
                        _persist_entry(idx)
                        st.rerun()


def handle_followup_input(idx: int, followup_question: str) -> None:
    """Runs when the user submits a message via chat_input while viewing an
    existing conversation -- answered ONLY from already-extracted findings
    (cheap), same cost accounting as a full search."""
    entry = st.session_state["search_history"][idx]
    if remaining_searches() <= 0:
        st.error(no_searches_left_message())
        return
    appropriate, reason = is_question_appropriate(followup_question)
    if not appropriate:
        st.error(f"لا يمكن معالجة هذا السؤال. {reason}")
        return
    record_search_used()
    backend.TOKEN_USAGE_LOG.clear()
    try:
        with st.spinner("جارٍ البحث عن إجابة..."):
            result = answer_followup(
                entry["question"], entry["stages"], followup_question,
                backend.answer_followup_question,
            )
    except PipelineError as error:
        print(f"[server-only log] PipelineError (followup): {error}")
        st.error("تعذّر الإجابة على هذا السؤال بالاعتماد على النتائج الحالية.")
    except ModelClientError as error:
        print(f"[server-only log] ModelClientError (followup): {error}")
        st.error("تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً.")
    else:
        entry.setdefault("followups", []).append({"question": followup_question, **result})
        _persist_entry(idx)
        st.rerun()


STAGE_LABELS = {
    1: "توليد الاستعلامات",
    2: "البحث في OpenAlex",
    3: "تصنيف الصلة",
    4: "اختيار الدراسات",
    5: "استخلاص الأدلة",
    6: "التوليف النهائي",
    7: "التحقق من النتائج",
}

def run_new_search(question: str) -> None:
    record_search_used()

    # Reset per-search so token usage doesn't accumulate across searches
    # in the same running app (Streamlit reruns the script, but the
    # imported run_assistant module -- and its state -- persists).
    backend.TOKEN_USAGE_LOG.clear()

    # user_message / technical_name are set on failure and rendered AFTER the
    # status block closes, so an error is never hidden inside a collapsed
    # status widget the user would have to re-expand.
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
        doc_id = history.save_search(st.session_state["user_email"], question, stages)
        st.session_state["search_history"].append(
            {"id": doc_id, "question": question, "stages": stages, "followups": []}
        )
        st.session_state["viewing_index"] = len(st.session_state["search_history"]) - 1
        st.rerun()


if st.session_state["viewing_index"] is not None:
    # Showing one conversation as a chat thread: the original question and
    # full report as the first exchange, then any follow-up Q&A after it.
    idx = st.session_state["viewing_index"]
    entry = st.session_state["search_history"][idx]

    st.chat_message("user").write(entry["question"])
    with st.chat_message("assistant"):
        render_result(entry["question"], entry["stages"])
        render_expand_button(idx)

    render_followup_thread(idx)

    st.caption(f"عمليات البحث المتبقية لحسابك: {remaining_searches()}")
    if followup_prompt := st.chat_input("اكتب سؤالاً إضافياً حول هذه النتائج..."):
        handle_followup_input(idx, followup_prompt)
else:
    st.caption(f"عمليات البحث المتبقية لحسابك: {remaining_searches()}")
    if new_question := st.chat_input(
        "اكتب سؤالك البحثي، مثال: ما تأثير استخدام الذكاء الاصطناعي التوليدي على التحصيل الأكاديمي لدى طلبة الجامعات؟"
    ):
        if remaining_searches() <= 0:
            st.error(no_searches_left_message())
        else:
            st.chat_message("user").write(new_question)
            with st.chat_message("assistant"):
                appropriate, reason = is_question_appropriate(new_question)
                if not appropriate:
                    st.error(f"لا يمكن معالجة هذا السؤال. {reason}")
                else:
                    run_new_search(new_question)
