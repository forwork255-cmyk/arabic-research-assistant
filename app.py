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

import base64
import os

import streamlit as st
import sentry_sdk

import run_assistant as backend
import auth
import history
import global_limit
import moderation
import paper_analysis
from pipeline_runner import run_pipeline, expand_selection, answer_followup, research_followup, PipelineError
from model_client import ModelClientError

st.set_page_config(page_title="مساعد البحث العلمي العربي", page_icon="📚", layout="centered")

# Error monitoring: reports unhandled exceptions to Sentry automatically, so
# a production failure is a real-time alert instead of something we only
# learn about if a user happens to mention it. Never sends research
# questions or any user-entered text -- just crash/error technical details.
# No DSN configured (e.g. running with no secrets set up at all) -> simply
# does nothing, rather than breaking the app.
_sentry_dsn = st.secrets.get("SENTRY_DSN", "")
if _sentry_dsn:
    sentry_sdk.init(dsn=_sentry_dsn, send_default_pii=False)


def _read_legal_doc(filename: str) -> str:
    # Strips the leading HTML comment (developer-only draft/review notes --
    # st.markdown doesn't parse HTML comments, so left as-is it would show
    # up as literal visible text instead of being hidden).
    path = os.path.join(os.path.dirname(__file__), filename)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.startswith("<!--"):
        end = text.find("-->")
        if end != -1:
            text = text[end + len("-->"):].lstrip()
    return text


def _start_session(email: str) -> None:
    """Marks this browser session as logged in AND issues a "remember me"
    token stored in the page URL (?t=...), so a page refresh restores the
    session automatically instead of asking to log in again every time."""
    email = email.strip().lower()
    st.session_state["authenticated"] = True
    st.session_state["user_email"] = email
    st.query_params["t"] = auth.create_session_token(email)


def show_login_and_signup() -> bool:
    """Email/password sign-up and login, backed by Firestore (see auth.py).
    Also tries to silently restore a previous session from the "remember me"
    token in the page URL before falling back to showing the login form."""
    if st.session_state.get("authenticated"):
        return True

    if not st.session_state.get("_tried_auto_login"):
        st.session_state["_tried_auto_login"] = True
        token = st.query_params.get("t")
        if token:
            email = auth.verify_session_token(token)
            if email:
                st.session_state["authenticated"] = True
                st.session_state["user_email"] = email
                return True
            # Stale/invalid token -- drop it so we don't keep re-checking it.
            del st.query_params["t"]

    st.title("📚 مساعد البحث العلمي العربي")
    login_tab, signup_tab = st.tabs(["تسجيل الدخول", "إنشاء حساب جديد"])

    with login_tab:
        email = st.text_input("البريد الإلكتروني", key="login_email")
        password = st.text_input("كلمة المرور", type="password", key="login_password")
        if st.button("دخول"):
            if auth.verify_login(email, password):
                _start_session(email)
                st.rerun()
            else:
                st.error("البريد الإلكتروني أو كلمة المرور غير صحيحة.")

    with signup_tab:
        new_email = st.text_input("البريد الإلكتروني", key="signup_email")
        new_password = st.text_input("كلمة المرور (8 أحرف على الأقل)", type="password", key="signup_password")
        with st.expander("شروط الاستخدام وسياسة الخصوصية"):
            st.markdown(_read_legal_doc("TERMS_OF_SERVICE.md"))
            st.divider()
            st.markdown(_read_legal_doc("PRIVACY_POLICY.md"))
        agreed = st.checkbox("أوافق على شروط الاستخدام وسياسة الخصوصية", key="signup_agree")
        if st.button("إنشاء حساب"):
            if not agreed:
                st.warning("يجب الموافقة على شروط الاستخدام وسياسة الخصوصية أولاً.")
            else:
                try:
                    auth.create_account(new_email, new_password)
                    _start_session(new_email)
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
        newest_first = list(range(len(st.session_state["search_history"]) - 1, -1, -1))
        starred_order = [i for i in newest_first if st.session_state["search_history"][i].get("starred")]
        other_order = [i for i in newest_first if not st.session_state["search_history"][i].get("starred")]

        def _render_history_row(i: int) -> None:
            # Two rows, not one, for each history item: the sidebar is forced
            # to a narrow fixed width (see the CSS below), and packing the
            # label + star + delete into a single row squeezed the icon
            # buttons down to ~15px wide -- too narrow for the browser to
            # render anything in them at all (blank boxes), regardless of
            # which character/emoji was used. Icons now get their own row
            # split only two/three ways, giving each one real room.
            entry = st.session_state["search_history"][i]
            label = entry["question"][:40] + ("…" if len(entry["question"]) > 40 else "")
            confirm_key = f"confirm_delete_{i}"

            if st.button(label, key=f"history_{i}", use_container_width=True):
                st.session_state["viewing_index"] = i
                st.rerun()

            if st.session_state.get(confirm_key):
                col_star, col_confirm, col_cancel = st.columns(3)
            else:
                col_star, col_delete = st.columns(2)

            with col_star:
                if st.button(
                    "⭐", key=f"star_{i}", help="إزالة من المفضلة" if entry.get("starred") else "تمييز كمفضلة",
                    type="primary" if entry.get("starred") else "secondary", use_container_width=True,
                ):
                    toggle_star(i)
                    st.rerun()
            if st.session_state.get(confirm_key):
                with col_confirm:
                    if st.button("✅", key=f"delete_confirm_{i}", help="تأكيد الحذف نهائياً", use_container_width=True):
                        if entry.get("id"):
                            history.delete_entry(st.session_state["user_email"], entry["id"])
                        del st.session_state["search_history"][i]
                        if st.session_state.get("viewing_index") == i:
                            st.session_state["viewing_index"] = None
                        elif st.session_state.get("viewing_index") is not None and st.session_state["viewing_index"] > i:
                            st.session_state["viewing_index"] -= 1
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                with col_cancel:
                    if st.button("❌", key=f"delete_cancel_{i}", help="إلغاء", use_container_width=True):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
            else:
                with col_delete:
                    if st.button("🗑️", key=f"delete_{i}", help="حذف", use_container_width=True):
                        st.session_state[confirm_key] = True
                        st.rerun()
            st.divider()

        if starred_order:
            st.caption("⭐ المفضلة")
            for i in starred_order:
                _render_history_row(i)
        for i in other_order:
            _render_history_row(i)
    st.caption("السجل محفوظ في حسابك، ويظهر عند تسجيل الدخول لاحقاً.")

    # Owner-only panel: manually grant subscription access to an account
    # after the owner has received payment outside the app (bank transfer,
    # cash, etc.) -- the same model as reselling a shared subscription seat.
    # No payment gateway involved; OWNER_EMAIL is a Streamlit secret so this
    # panel is invisible/inert for every other account.
    owner_email = st.secrets.get("OWNER_EMAIL", "")
    if owner_email and st.session_state["user_email"] == owner_email.strip().lower():
        st.divider()
        with st.expander("لوحة المالك: منح اشتراك"):
            grant_email = st.text_input("البريد الإلكتروني للمستخدم", key="grant_email")
            grant_days = st.number_input("عدد الأيام", min_value=1, value=30, step=1, key="grant_days")
            grant_plan = st.selectbox(
                "الخطة", options=list(auth.PLANS), index=0, key="grant_plan",
                help="عادي: نفس النموذج المستخدم لكل الحسابات. المتقدم/الأقصى: نموذج أقوى وأعلى تكلفة لكل عملية بحث.",
            )
            if st.button("منح الاشتراك", key="grant_button"):
                try:
                    auth.grant_subscription(grant_email, int(grant_days), plan=grant_plan)
                    st.success(f"تم منح الاشتراك ({grant_plan}) لـ {grant_email.strip().lower()}.")
                except auth.AuthError as e:
                    st.error(str(e))

    st.divider()
    if st.button("تسجيل الخروج", use_container_width=True):
        auth.clear_session_token(st.session_state["user_email"])
        st.query_params.clear()
        st.session_state.clear()
        st.rerun()


_OWNER_SENTINEL = 999_999  # effectively unlimited -- the owner only, not regular subscribers


def is_owner() -> bool:
    owner_email = st.secrets.get("OWNER_EMAIL", "")
    return bool(owner_email) and st.session_state["user_email"] == owner_email.strip().lower()


def current_plan() -> str:
    """Which model tier the logged-in account's searches should use. Only a
    subscribed account's own plan affects model choice -- a free-tier account
    (or a subscription that expired) always gets "normal", regardless of any
    leftover "plan" value on the account. Used for every real model call the
    user can trigger (new search, expand, follow-up, research-escalation),
    not just the main search -- a "max" subscriber should get max-quality
    answers throughout the whole conversation, not just the first message."""
    account = auth.get_account(st.session_state["user_email"])
    return account.get("plan", "normal") if account and auth.is_subscribed(account) else "normal"


def remaining_searches() -> int:
    """
    How many searches are left on the currently logged-in account -- 0 if the
    site-wide emergency cap (global_limit.py) has been reached, regardless of
    this account's own remaining allowance. That cap protects the real API
    budget from being drained by many accounts/sign-ups at once, not just
    one, and applies to every account including subscribers and the owner --
    a real budget safety net shouldn't have an exception for anyone.

    A subscribed (paying) account gets a separate, generous-but-finite
    allowance (auth.SUBSCRIPTION_SEARCH_LIMITS[plan] per paid period) -- like
    a real paid plan, not literally unlimited, so no single subscriber can
    exhaust the whole site's budget alone.
    """
    if global_limit.global_limit_reached():
        return 0
    if is_owner():
        return _OWNER_SENTINEL
    account = auth.get_account(st.session_state["user_email"])
    if account is None:
        return 0
    if auth.is_subscribed(account):
        return auth.subscription_searches_remaining(account)
    return max(0, account["search_limit"] - account["used"])


def record_search_used() -> None:
    account = auth.get_account(st.session_state["user_email"])
    if account and auth.is_subscribed(account):
        auth.increment_subscription_used(st.session_state["user_email"])
    else:
        auth.increment_used(st.session_state["user_email"])
    global_limit.increment_global_used()


def no_searches_left_message() -> str:
    if global_limit.global_limit_reached():
        return "بلغ التطبيق الحد الأقصى المؤقت لعدد عمليات البحث. يرجى المحاولة لاحقاً."
    return "لقد استنفدت عدد عمليات البحث المسموح بها لحسابك."


def searches_caption() -> str:
    """What to show near the chat input: owner/subscription status if
    applicable, otherwise the remaining-searches count."""
    if is_owner():
        return "أنت المالك — وصول غير محدود (يبقى محمياً بحد الأمان العام للتطبيق)."
    account = auth.get_account(st.session_state["user_email"])
    if account and auth.is_subscribed(account):
        until = account["subscribed_until"].strftime("%Y-%m-%d")
        left = auth.subscription_searches_remaining(account)
        plan = account.get("plan", "normal")
        return f"اشتراكك فعّال حتى {until} — خطة {plan} — {left} عملية بحث متبقية لهذه الفترة."
    return f"عمليات البحث المتبقية لحسابك: {remaining_searches()}"


def is_question_appropriate(question: str, prompt_builder=moderation.format_moderation_prompt) -> tuple:
    """
    Safety check run BEFORE the real pipeline/follow-up call and before it
    counts against the search limit (see moderation.py). Fails OPEN (allows
    the question through) if the moderation call itself errors or returns a
    malformed result -- a legitimate user should not be blocked by an
    infrastructure hiccup; the per-account and site-wide search caps remain
    the primary defense against cost abuse.

    prompt_builder defaults to judging a freestanding research question;
    pass moderation.format_paper_question_moderation_prompt for text
    accompanying an uploaded paper, which must not be held to that same bar
    (a short caption like "summarize" is normal there, not suspicious).
    """
    try:
        result = backend.check_question_moderation(prompt_builder(question))
    except ModelClientError as error:
        print(f"[server-only log] Moderation check failed, allowing through: {error}")
        sentry_sdk.capture_exception(error)
        return True, None
    if not moderation.validate_moderation_output(result):
        print(f"[server-only log] Moderation returned malformed output, allowing through: {result}")
        sentry_sdk.capture_message(f"Moderation returned malformed output: {result}")
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


def render_token_usage(token_usage: list | None) -> None:
    """Renders a token-usage expander for an explicit snapshot (NOT the
    live backend.TOKEN_USAGE_LOG) -- that global always holds whatever
    action ran most recently, so reading it directly here would show a
    follow-up's usage under the original result, or vice versa, whichever
    ran last. Each caller must pass its own snapshot, taken right after its
    own model call(s) completed."""
    if not token_usage:
        return
    with st.expander("استخدام الرموز / Token usage"):
        total_in = total_out = 0
        for usage_row in token_usage:
            st.write(f"{usage_row['stage']} ({usage_row['model']}): {usage_row['input_tokens']} in / {usage_row['output_tokens']} out")
            total_in += usage_row["input_tokens"]
            total_out += usage_row["output_tokens"]
        st.write(f"**المجموع:** {total_in} input / {total_out} output tokens")


def render_result(question: str, stages: dict, token_usage: list | None = None) -> None:
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

    render_token_usage(token_usage)


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


def toggle_star(idx: int) -> None:
    entry = st.session_state["search_history"][idx]
    new_starred = not entry.get("starred", False)
    entry["starred"] = new_starred
    if entry.get("id"):
        history.set_starred(st.session_state["user_email"], entry["id"], new_starred)


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
        plan = current_plan()
        try:
            with st.spinner("جارٍ إضافة المزيد من الدراسات..."):
                new_stages = expand_selection(
                    entry["question"], entry["stages"],
                    backend.extract_findings, backend.make_synthesizer(plan),
                )
        except PipelineError as error:
            print(f"[server-only log] PipelineError (expand): {error}")
            sentry_sdk.capture_exception(error)
            st.error("تعذّر إضافة المزيد من الدراسات. قد لا توجد دراسات إضافية متاحة.")
        except ModelClientError as error:
            print(f"[server-only log] ModelClientError (expand): {error}")
            sentry_sdk.capture_exception(error)
            st.error("تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً.")
        else:
            st.session_state["search_history"][idx]["stages"] = new_stages
            st.session_state["search_history"][idx]["token_usage"] = list(backend.TOKEN_USAGE_LOG)
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
            render_token_usage(fu.get("token_usage"))

            if fu.get("sufficient") is False and not fu.get("researched"):
                st.caption("لم تكن النتائج الحالية كافية للإجابة على هذا السؤال.")
                if remaining_searches() <= 0:
                    st.caption(no_searches_left_message())
                elif st.button("ابحث عن دراسات جديدة لهذا السؤال (تكلفة إضافية)", key=f"research_followup_{idx}_{i}"):
                    record_search_used()
                    backend.TOKEN_USAGE_LOG.clear()
                    plan = current_plan()
                    try:
                        with st.spinner("جارٍ البحث عن دراسات جديدة..."):
                            new_result = research_followup(
                                entry["question"], entry["stages"], fu["question"],
                                backend.generate_queries, backend.make_relevance_classifier(plan),
                                backend.extract_findings, backend.make_followup_answerer(plan),
                            )
                    except PipelineError as error:
                        print(f"[server-only log] PipelineError (research_followup): {error}")
                        sentry_sdk.capture_exception(error)
                        st.error("تعذّر العثور على دراسات جديدة مناسبة لهذا السؤال.")
                    except ModelClientError as error:
                        print(f"[server-only log] ModelClientError (research_followup): {error}")
                        sentry_sdk.capture_exception(error)
                        st.error("تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً.")
                    else:
                        new_result["researched"] = True
                        new_result["token_usage"] = list(backend.TOKEN_USAGE_LOG)
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
    appropriate, reason = is_question_appropriate(
        followup_question, prompt_builder=moderation.format_followup_moderation_prompt
    )
    if not appropriate:
        st.error(f"لا يمكن معالجة هذا السؤال. {reason}")
        return
    record_search_used()
    backend.TOKEN_USAGE_LOG.clear()
    try:
        with st.spinner("جارٍ البحث عن إجابة..."):
            result = answer_followup(
                entry["question"], entry["stages"], followup_question,
                backend.make_followup_answerer(current_plan()),
            )
    except PipelineError as error:
        print(f"[server-only log] PipelineError (followup): {error}")
        sentry_sdk.capture_exception(error)
        st.error("تعذّر الإجابة على هذا السؤال بالاعتماد على النتائج الحالية.")
    except ModelClientError as error:
        print(f"[server-only log] ModelClientError (followup): {error}")
        sentry_sdk.capture_exception(error)
        st.error("تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً.")
    else:
        result["token_usage"] = list(backend.TOKEN_USAGE_LOG)
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

def render_paper_analysis(stages: dict) -> None:
    st.caption(f"📄 {stages['filename']}")
    st.write(stages["analysis"])
    st.caption("هذا التحليل مبني فقط على محتوى الملف المرفق.")


def render_paper_followup_thread(idx: int) -> None:
    entry = st.session_state["search_history"][idx]
    for fu in entry.get("followups", []):
        st.chat_message("user").write(fu["question"])
        with st.chat_message("assistant"):
            st.write(fu["answer"])


def handle_paper_followup_input(idx: int, followup_question: str) -> None:
    """Runs when the user asks another question about an already-analyzed
    paper. The PDF itself is only cached in-session (st.session_state), never
    written to Firestore -- a 15MB file would blow past Firestore's 1MB
    document size limit. So this only works while the file is still cached
    from the original upload in this running session."""
    entry = st.session_state["search_history"][idx]
    pdf_base64 = st.session_state.get("paper_pdf_cache", {}).get(entry.get("id"))
    if not pdf_base64:
        st.error("لم يعد الملف متاحاً في هذه الجلسة. يرجى رفعه مرة أخرى لطرح سؤال جديد عنه.")
        return
    if remaining_searches() <= 0:
        st.error(no_searches_left_message())
        return
    appropriate, reason = is_question_appropriate(
        followup_question, prompt_builder=moderation.format_paper_question_moderation_prompt
    )
    if not appropriate:
        st.error(f"لا يمكن معالجة هذا السؤال. {reason}")
        return
    record_search_used()
    backend.TOKEN_USAGE_LOG.clear()
    prompt = paper_analysis.format_paper_followup_prompt(entry["stages"]["analysis"], followup_question)
    try:
        with st.spinner("جارٍ البحث عن إجابة..."):
            answer = backend.analyze_paper(prompt, pdf_base64)
    except ModelClientError as error:
        print(f"[server-only log] ModelClientError (paper followup): {error}")
        sentry_sdk.capture_exception(error)
        st.error("تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً.")
    else:
        entry.setdefault("followups", []).append({"question": followup_question, "answer": answer})
        _persist_entry(idx)
        st.rerun()


def run_paper_analysis(pdf_bytes: bytes, filename: str, question: str) -> None:
    record_search_used()
    backend.TOKEN_USAGE_LOG.clear()

    pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    prompt = paper_analysis.format_paper_analysis_prompt(question or None)

    analysis_text = None
    user_message = None
    technical_name = None

    with st.spinner("جارٍ تحليل الورقة البحثية..."):
        try:
            analysis_text = backend.analyze_paper(prompt, pdf_base64)
        except ModelClientError as error:
            print(f"[server-only log] ModelClientError (paper analysis): {error}")
            sentry_sdk.capture_exception(error)
            user_message = "تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً."
            technical_name = type(error).__name__
        except Exception as error:
            print(f"[server-only log] Unexpected error (paper analysis): {error}")
            sentry_sdk.capture_exception(error)
            user_message = "حدث خطأ غير متوقع أثناء تحليل الورقة."
            technical_name = type(error).__name__

    if user_message:
        st.error(user_message)
        with st.expander("تفاصيل تقنية"):
            st.caption(technical_name)
    else:
        label = question if question else f"تحليل: {filename}"
        stages = {"kind": "paper_analysis", "filename": filename, "analysis": analysis_text}
        doc_id = history.save_search(st.session_state["user_email"], label, stages)
        st.session_state.setdefault("paper_pdf_cache", {})[doc_id] = pdf_base64
        st.session_state["search_history"].append(
            {"id": doc_id, "question": label, "stages": stages, "followups": [], "starred": False}
        )
        st.session_state["viewing_index"] = len(st.session_state["search_history"]) - 1
        st.rerun()


def run_new_search(question: str) -> None:
    record_search_used()

    # Reset per-search so token usage doesn't accumulate across searches
    # in the same running app (Streamlit reruns the script, but the
    # imported run_assistant module -- and its state -- persists).
    backend.TOKEN_USAGE_LOG.clear()

    plan = current_plan()

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
                relevance_classifier=backend.make_relevance_classifier(plan),
                extractor=backend.extract_findings,
                synthesizer=backend.make_synthesizer(plan),
                progress=on_progress,
            )
        except ModelClientError as error:
            print(f"[server-only log] ModelClientError: {error}")  # console only, never shown in the browser
            sentry_sdk.capture_exception(error)
            status.update(label="تعذّر إتمام البحث", state="error", expanded=False)
            user_message = "تعذّر الاتصال بنموذج الذكاء الاصطناعي. يرجى المحاولة مرة أخرى لاحقاً."
            technical_name = type(error).__name__
        except PipelineError as error:
            print(f"[server-only log] PipelineError: {error}")  # console only, never shown in the browser
            sentry_sdk.capture_exception(error)
            status.update(label="تعذّر إتمام البحث", state="error", expanded=False)
            user_message = "تعذّر إكمال معالجة النتائج. يرجى المحاولة مرة أخرى أو تعديل السؤال."
            technical_name = type(error).__name__
        except Exception as error:
            # Safety net: never show a raw traceback or internal details to the user.
            print(f"[server-only log] Unexpected error: {error}")
            sentry_sdk.capture_exception(error)
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
        st.session_state["search_history"].append({
            "id": doc_id, "question": question, "stages": stages, "followups": [], "starred": False,
            "token_usage": list(backend.TOKEN_USAGE_LOG),
        })
        st.session_state["viewing_index"] = len(st.session_state["search_history"]) - 1
        st.rerun()


if st.session_state["viewing_index"] is not None:
    # Showing one conversation as a chat thread: the original question and
    # full report as the first exchange, then any follow-up Q&A after it.
    idx = st.session_state["viewing_index"]
    entry = st.session_state["search_history"][idx]

    star_col, _ = st.columns([2, 8])
    with star_col:
        label = "⭐ مفضلة" if entry.get("starred") else "⭐ تمييز كمفضلة"
        if st.button(label, key=f"view_star_{idx}", type="primary" if entry.get("starred") else "secondary"):
            toggle_star(idx)
            st.rerun()

    st.chat_message("user").write(entry["question"])
    with st.chat_message("assistant"):
        if entry["stages"].get("kind") == "paper_analysis":
            render_paper_analysis(entry["stages"])
        else:
            render_result(entry["question"], entry["stages"], entry.get("token_usage"))
            render_expand_button(idx)

    if entry["stages"].get("kind") == "paper_analysis":
        render_paper_followup_thread(idx)
    else:
        render_followup_thread(idx)

    st.caption(searches_caption())
    if entry["stages"].get("kind") == "paper_analysis":
        if entry.get("id") in st.session_state.get("paper_pdf_cache", {}):
            if followup_prompt := st.chat_input("اكتب سؤالاً إضافياً حول هذه الورقة..."):
                handle_paper_followup_input(idx, followup_prompt)
        else:
            st.caption("لطرح سؤال جديد حول هذه الورقة، يرجى رفعها مرة أخرى في محادثة جديدة.")
    else:
        if followup_prompt := st.chat_input("اكتب سؤالاً إضافياً حول هذه النتائج..."):
            handle_followup_input(idx, followup_prompt)
else:
    st.caption(searches_caption())
    st.caption("يمكنك أيضاً إرفاق ورقة بحثية (PDF) لتحليلها مباشرة، مع سؤال أو بدونه.")
    if submitted := st.chat_input(
        "اكتب سؤالك البحثي، مثال: ما تأثير استخدام الذكاء الاصطناعي التوليدي على التحصيل الأكاديمي لدى طلبة الجامعات؟",
        accept_file=True, file_type=["pdf"],
    ):
        question_text = submitted.text.strip()
        uploaded_files = submitted.files

        if remaining_searches() <= 0:
            st.error(no_searches_left_message())
        elif uploaded_files:
            pdf_file = uploaded_files[0]
            pdf_bytes = pdf_file.getvalue()
            if len(pdf_bytes) > paper_analysis.MAX_PDF_BYTES:
                st.error(
                    f"حجم الملف كبير جداً (الحد الأقصى {paper_analysis.MAX_PDF_BYTES // (1024 * 1024)} ميغابايت)."
                )
            else:
                appropriate, reason = (True, None)
                if question_text:
                    appropriate, reason = is_question_appropriate(
                        question_text, prompt_builder=moderation.format_paper_question_moderation_prompt
                    )
                if not appropriate:
                    st.error(f"لا يمكن معالجة هذا السؤال. {reason}")
                else:
                    st.chat_message("user").write(question_text or f"📄 {pdf_file.name}")
                    with st.chat_message("assistant"):
                        run_paper_analysis(pdf_bytes, pdf_file.name, question_text)
        elif question_text:
            st.chat_message("user").write(question_text)
            with st.chat_message("assistant"):
                appropriate, reason = is_question_appropriate(question_text)
                if not appropriate:
                    st.error(f"لا يمكن معالجة هذا السؤال. {reason}")
                else:
                    run_new_search(question_text)
        else:
            st.warning("الرجاء إدخال سؤال أو إرفاق ورقة بحثية.")
