import { useEffect, useRef, useState } from "react";
import { CalendarDays, FileText, Moon, Sparkles, Sun, Terminal } from "lucide-react";
import { AppShell, BlockState, BottomNav, Breadcrumb, Header, IconButton, NavItem, Search, Sidebar } from "../vendor/comp-lib/src/index.ts";
import { currentSearch, decisionAlreadyRecorded, feedbackContextIsCurrent, feedbackScopeKey, picturePresentation, prefillReviewKey, prefillReviewScopeKey, requestedView, reviewWriteReducer, seedPrefillReviews, seedProposalReviews, viewAddress } from "./challenge-demo.mjs";
import { activeRoleFromRoster, roleLabelFromRoster, worklistFor } from "./worklist.mjs";
import { ChatPanel } from "./ChatPanel.jsx";
import { ASSISTANT_PANE_ID, AssistantPane, EncounterContext, PictureBriefing } from "./ChallengeDemo.jsx";
import { TechnicalView } from "./TechnicalView.jsx";
import { DayView } from "./DayView.jsx";

const API = import.meta.env.VITE_API ?? "";
const REQUEST_DEADLINE_MS = 180_000;
// The shell's bootstrap asks for the largest page the API accepts. The operator catalogue is bounded at
// 128 rows by the import (`backend/app/delivery.py`), so one page is the whole catalogue and the shell's
// selection, and the same-person lens siblings all see it — while a catalogue that ever
// exceeded the maximum would arrive as a short page with `total` above `count` rather than as a silent
// claim of completeness. The worklist pages with its own, smaller page size.
const CATALOGUE_PAGE_SIZE = 200;
// The worklist's page: the unit the server's `offset` walks and the thing a `q` search narrows server-side.
const WORKLIST_PAGE_SIZE = 25;
const EMPTY_WORKLIST = { encounters: [], total: 0, matching: null };
const EMPTY_DAY = { rows: [], days: [], ready: 0 };
// Today, as the ISO date the worklist route takes. Local time: the professional's day, not UTC's.
function todayIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}
const dayRequestKey = (role, day) => JSON.stringify([role, day]);
// The identity of a worklist request: a response is only rendered under the lens, search and offset it was
// asked for, so a page in flight for a previous lens can never be counted or offered under the next one.
const worklistRequestKey = (role, query, offset) => JSON.stringify([role, query, offset]);

function responseError(response, payload) {
  return payload?.detail || `Taustapalvelu vastasi virheellä ${response.status}.`;
}

function newEventId() {
  return crypto.randomUUID();
}

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

// `same-document-transitions`: "Wrap the logic that updates the DOM in `document.startViewTransition`.
// The browser captures a snapshot of the current state, runs the update, and then captures the new state."
// It is used exactly where the sheet already animates but the browser cannot: the theme swap is a swap of
// every custom property on <html>, and those are `var()` aliases resolved at used-value time, so a
// `transition: background-color` on the app root has no interpolable value of its own — the swap was a
// single instant repaint of a document-reading page. The view transition cross-fades the two paints instead.
// (This is a reading of the cascade, not a browser measurement: nothing in this repository can paint.)
//
// The guide's fallback is the current behaviour and is the first branch below: an engine without
// `document.startViewTransition` (or a reader who asked for reduced motion, which `motion` requires us to
// honour) runs the callback directly and the theme changes in one frame, exactly as before.
//
// The reduced-motion half is enforced in TWO places, and neither is an app rule. The scripted guard is the
// `matchMedia` in this function. The CSS half is the vendored base layer's own
// `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition-duration: 0s !important;
// animation-duration: 0s !important } }` — it deliberately names `::before`/`::after`, and the view
// transition's own pseudo-elements are part of the originating element's tree. An app rule could not cover
// it: `::view-transition-group(*)` is not inside `.brief-app`, so any app selector for it is refused by the
// design-system scope rule (an app rule may only be app-root scoped or a `.brief-*` class). That is a real
// conflict between the design-system gate and `motion`, and the resolution is to leave the pseudo-element
// rule to the pinned library rather than widen the gate or hide the selector in a file the gate cannot see.
// `modern-web.test.mjs` pins both halves of that arrangement.
//
// The guide's "MANDATORY Accessibility Routing" is not applied here and the reason is structural: focus is
// routed to a newly revealed heading when a transition *replaces* the interactive surface. This one does
// not — the control that was just pressed stays in the DOM and keeps focus, so routing focus anywhere else
// would move the reader out of the toolbar they are standing in.
function startViewTransition(update) {
  const reduced = globalThis.matchMedia?.(REDUCED_MOTION).matches ?? false;
  if (reduced || typeof document === "undefined" || typeof document.startViewTransition !== "function") {
    update();
    return;
  }
  document.startViewTransition(update);
}

// The phone breakpoint the library's own sheets use for the bottom bar and the hidden rail. The library
// `Header` has a fixed anatomy (breadcrumb, title, user with role), which measured 189 px tall at 390 px and
// 205 px with a role — more than the two rows a phone can give a header. The breadcrumb and the role are left
// out under 640 px through this hook rather than through CSS over the vendored component, which the
// design-system gate refuses. Without `matchMedia` (a server render, jsdom) the answer is false, so a static
// render is the desktop header and the audits see the breadcrumb landmark.
const PHONE = "(max-width: 639px)";

function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => (typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(query).matches : false));
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const list = window.matchMedia(query);
    const update = event => setMatches(event.matches);
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

export default function App() {
  // The catalogue page the shell holds, and the worklist page its own panel holds. They are separate
  // requests on purpose: the shell needs the whole bounded catalogue (selection, lens siblings),
  // while the worklist is role-scoped and server-paged, so a catalogue larger than one page still yields a
  // correct, counted worklist without the browser ever reducing over the full list.
  const [catalogue, setCatalogue] = useState({ encounters: [], total: 0, aggregate: null });
  const [worklistResponse, setWorklistResponse] = useState(EMPTY_WORKLIST);
  const [worklistOffset, setWorklistOffset] = useState(0);
  // The day the Työpäivä view shows, and that day's rows from `/api/worklist`, keyed by the lens and the day
  // they were asked for so a late page for another lens is never shown under this one.
  const [day, setDay] = useState(() => requestedView(currentSearch()).day || todayIso());
  const [dayResponse, setDayResponse] = useState(EMPTY_DAY);
  // The server-side search term. `pickerQuery` is what the professional types; this is the debounced value
  // the worklist request carries, so a keystroke is not a request.
  const [searchQuery, setSearchQuery] = useState("");
  const [encounterId, setEncounterId] = useState("");
  const [roleChoice, setRoleChoice] = useState("");
  const [mode, setMode] = useState("normal");
  const [pickerQuery, setPickerQuery] = useState("");
  // "day", "briefing" or "technical". The technical page is a destination like the other two (ADR-0028's
  // shell navigates between pages, not disclosures), addressed as `?page=technical`.
  const [destination, setDestination] = useState("briefing");
  // The assistant pane (night plan N1): open when the address says `assistant=1`, so a reload and a shared link
  // keep it. The address helpers in challenge-demo.mjs own the view selectors; this one flag is read and written
  // beside them here, in `writeAddress`, so every rewrite of the address carries it.
  const [assistantOpen, setAssistantOpen] = useState(() => assistantAddressed(currentSearch()));
  // The control that opened the pane, so closing can hand focus back to it; and the move the next render owes:
  // into the pane on open, back to the opener on close. A move that the address made (Back, a reload) owes none.
  const assistantOpener = useRef(null);
  const assistantFocusDue = useRef(null);
  const assistantHeading = useRef(null);
  const assistantHeaderToggle = useRef(null);
  const phone = useMediaQuery(PHONE);
  // Read presentation selectors once. Encounter still has to match an API-returned row; these values
  // never affect authorization or which records the API returns.
  const [pageOptions, setPageOptions] = useState(() => {
    const view = requestedView(currentSearch());
    if (typeof window === "undefined" || new URLSearchParams(currentSearch()).has("theme")) return view;
    let remembered = null;
    try { remembered = window.localStorage?.getItem("luvn-theme"); } catch { /* storage may be unavailable */ }
    const preferred = remembered === "dark" || remembered === "light" ? remembered
      : (window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "dark" : "light");
    return { ...view, theme: preferred };
  });
  const [picture, setPicture] = useState(null);
  const [loading, setLoading] = useState(false);
  // Bumped by the pre-fill block's retry: the picture effect re-runs and fetches the same route again. The
  // backend has no refresh parameter, so this is a plain re-fetch of `(encounter, mode)`; the picture on
  // screen stays until the new one arrives.
  const [pictureAttempt, setPictureAttempt] = useState(0);
  const [error, setError] = useState("");
  // The address named an encounter this catalogue does not carry. The briefing says so; it loads nothing.
  const [unknownEncounter, setUnknownEncounter] = useState(false);
  const [reviewRequests, setReviewRequests] = useState({});
  // The caller's own recorded decisions for the open encounter, as `GET …/decisions` returned them. Fetched
  // once per encounter here and handed to both rails: the proposal rail seeds `reviewRequests` from it and
  // the assistant rail seeds its own map from the same payload, so the route is not asked twice for one open.
  const [decisions, setDecisions] = useState(null);
  // The caller's own verdicts on the pre-filled form (`GET …/prefill/reviews`), keyed by form and question,
  // fetched once per encounter open beside the decisions and seeded into the same shape a live save reaches.
  const [prefillReviews, setPrefillReviews] = useState({});
  const [failureComparison, setFailureComparison] = useState(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState("");
  // There is deliberately no `currentEncounter` ref: the addressed encounter lives in `encounterId` and in
  // the URL, which is what the effects read, so a third copy could only disagree with them.
  const contextGeneration = useRef(0);
  const pendingReviewEvents = useRef(new Map());

  // Derived before the effects that depend on them. The roster is the server's whole-catalogue
  // `aggregate.roster`, not a reduction over the loaded rows, so the Rooli selector offers exactly the
  // professional roles the catalogue carries even when the page does not carry all of them.
  const encounters = catalogue.encounters;
  const roster = catalogue.aggregate?.roster ?? [];
  const selected = encounters.find(item => item.encounter_id === encounterId);
  // The professional's lens: the role they chose (URL or picker) when the catalogue carries it, else the
  // role of the addressed encounter, else the first role on offer. Held during a briefing so the workspace
  // does not silently switch who it is looking through.
  const activeRole = activeRoleFromRoster(roster, selected?.role || roleChoice || "");
  const activeRoleLabel = roleLabelFromRoster(roster, activeRole);

  // Apply the view an address names. The first load and Back/Forward both come through here, so a history
  // entry restores exactly the view it was written with. Nothing is substituted: an encounter the catalogue
  // does not carry is a neutral selection (no Picture), and a role with no encounter is that role's worklist.
  function applyView(view, currentEncounterId) {
    // The theme is the address's when it names one; otherwise the one already chosen (remembered on the
    // device or the system's), which the bootstrap must not reset to the address helper's default.
    const named = new URLSearchParams(currentSearch()).has("theme") || new URLSearchParams(currentSearch()).has("mode");
    setPageOptions(current => ({ ...view, theme: named ? view.theme : current.theme }));
    setMode(view.mode);
    setRoleChoice(view.role);
    // A bare address lands on the day (the sidebar's first destination); an address that names an encounter
    // opens that briefing, or its technical page when the address says `page=technical`; a role alone is that
    // role's day. The default encounter still loads behind the day view, so Tilannekuva is ready when it is
    // chosen. The technical page needs an addressed encounter: it describes one Picture's production.
    setDestination(view.encounterAddressed ? (view.page === "technical" ? "technical" : "briefing") : "day");
    setAssistantOpen(assistantAddressed(currentSearch()));
    if (view.day) setDay(view.day);
    setUnknownEncounter(view.encounterUnknown);
    if (view.encounterId === currentEncounterId) return;
    contextGeneration.current += 1;
    setEncounterId(view.encounterId);
    setLoading(Boolean(view.encounterId));
    setPicture(null);
    setReviewRequests({});
    setPrefillReviews({});
    setDecisions(null);
    setFailureComparison(null);
    setComparisonError("");
    setError("");
    setWorklistOffset(0);
  }

  // The one address writer. It writes the whole address from the state the view renders from, so the two
  // cannot disagree. `replace` is for a change the reader did not navigate to (a theme or mode toggle); a
  // selection PUSHES, so Back returns to what they were looking at. The fragment is kept unless the caller
  // names a new one: a stale `#source-<id>` must not survive an encounter change.
  function writeAddress(fields, { replace = false, hash = window.location.hash } = {}) {
    // The day survives an address rewrite that does not name one (a theme or mode toggle on the day view), and
    // is dropped when an encounter is addressed: a briefing's address is the encounter, not a day. Read from the
    // address itself rather than from state, so this writer depends on its arguments and the location alone.
    const keptDay = fields.encounterId ? undefined
      : (fields.day ?? new URLSearchParams(window.location.search).get("day") ?? undefined);
    // The pane flag survives every rewrite unless the caller sets it: a selection or a theme toggle does not
    // close the assistant. `viewAddress` ignores the key, so the flag is appended after it. Read inline, not
    // through `assistantAddressed`: modern-web.test.mjs runs this writer on its own, with the location alone.
    const assistant = fields.assistant ?? new URLSearchParams(window.location.search).has("assistant");
    const view = viewAddress({ ...fields, day: keptDay });
    const search = assistant ? `${view ? `${view}&` : "?"}assistant=1` : view;
    const url = `${window.location.pathname}${search}${hash}`;
    if (replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
  }

  // The one entry point for opening a case: a worklist row, the dashboard, or the picker. It pushes the
  // address carrying the encounter, its own role and the presentation the reader is in. Choosing the case
  // that is already open still writes the address: a bare '/' shows the default case without naming it, so
  // the explicit choice has to be recorded here or a reload would re-derive a different default. Only the
  // state resets are skipped for an unchanged encounter, because the picture is already loaded for it.
  function openEncounter(id) {
    setDestination("briefing");
    const known = encounters.find(item => item.encounter_id === id);
    const role = known?.role ?? roleChoice;
    setRoleChoice(role);
    // The catalogue page is the first slice of the caller's scope, and a day row or a search hit can name an
    // encounter outside it. The briefing renders from the catalogue row (labels, lens siblings), so the row is
    // resolved by id and added to the catalogue -- the same resolution a deep link gets on load.
    if (!known) {
      fetch(`${API}/api/encounters/${encodeURIComponent(id)}`)
        .then(response => (response.ok ? response.json() : null))
        .then(row => {
          if (!row?.encounter_id) return;
          setCatalogue(current => (current.encounters.some(item => item.encounter_id === row.encounter_id)
            ? current : { ...current, encounters: [...current.encounters, row] }));
          setRoleChoice(current => current || row.role);
        })
        .catch(() => {});
    }
    if (id !== encounterId) {
      contextGeneration.current += 1;
      setUnknownEncounter(false);
      setEncounterId(id);
      setLoading(true);
      setPicture(null);
      setReviewRequests({});
      setPrefillReviews({});
      setDecisions(null);
      setFailureComparison(null);
      setComparisonError("");
      setError("");
      // Opening a case can change the active lens, which restarts the worklist's page walk.
      setWorklistOffset(0);
    }
    writeAddress({ encounterId: id, role, mode, theme: pageOptions.theme, capture: pageOptions.capture },
      { hash: "#brief-content" });
  }

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API}/api/encounters?limit=${CATALOGUE_PAGE_SIZE}&offset=0`, { signal: controller.signal })
      .then(async response => {
        const payload = await response.json();
        if (!response.ok) throw new Error(responseError(response, payload));
        return payload;
      })
      .then(async payload => {
        setCatalogue(payload);
        const view = requestedView(currentSearch(), payload.encounters);
        // A deep link to an encounter beyond the bootstrapped page is resolved BY ID rather than called
        // absent. The page is a first slice of the caller's scope, and `GET /api/encounters/{id}` answers for
        // any id in it with the same row shape -- so a valid link opens instead of claiming the person is not
        // in the dataset. A refusal (outside the policy's scope, or no such encounter) returns the same body
        // for both, so the resolution cannot become an existence oracle: nothing is passed, and the address
        // stays unknown exactly as before.
        if (view.encounterUnknown && view.encounterWanted) {
          try {
            const response = await fetch(`${API}/api/encounters/${encodeURIComponent(view.encounterWanted)}`);
            if (response.ok) {
              const row = await response.json();
              if (row?.encounter_id) {
                // The briefing renders from its catalogue row, so the resolved row joins the page exactly as
                // openEncounter() adds a day row or a search hit the page did not carry.
                setCatalogue(current => (current.encounters.some(item => item.encounter_id === row.encounter_id)
                  ? current : { ...current, encounters: [...current.encounters, row] }));
              }
              applyView(requestedView(currentSearch(), [...payload.encounters, row], row), "");
              return;
            }
          } catch {
            // A transport failure is not evidence the encounter is absent; fall through to the unknown state.
          }
        }
        applyView(view, "");
      })
      .catch(reason => {
        if (reason.name !== "AbortError") setError("Palvelutilanteiden lataus epäonnistui.");
      });
    return () => controller.abort();
  }, []);

  // Back and Forward restore the view the history entry names. Re-subscribed when the catalogue or the open
  // encounter changes, so the handler never reads a stale list or a stale encounter.
  useEffect(() => {
    const onPopState = () => applyView(requestedView(window.location.search, encounters), encounterId);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [encounters, encounterId]);

  // The worklist is a real server page: the role scope, the search and the offset are the request's own
  // parameters, so the panel never reduces over a list the browser had to fetch in full to count. The
  // responses carry the key they were requested under, and `worklistPage` below only accepts the one that
  // matches the current lens, so an in-flight page for a previous lens is ignored rather than cleared by
  // a second render — which is also why this effect never calls setState before its request resolves.
  useEffect(() => {
    const handle = window.setTimeout(() => setSearchQuery(pickerQuery.trim()), 250);
    return () => window.clearTimeout(handle);
  }, [pickerQuery]);

  useEffect(() => {
    if (!activeRole) return undefined;
    const controller = new AbortController();
    const key = worklistRequestKey(activeRole, searchQuery, worklistOffset);
    const params = new URLSearchParams({
      role: activeRole, limit: String(WORKLIST_PAGE_SIZE), offset: String(worklistOffset),
    });
    if (searchQuery) params.set("q", searchQuery);
    fetch(`${API}/api/encounters?${params}`, { signal: controller.signal })
      .then(async response => {
        const payload = await response.json();
        if (!response.ok) throw new Error(responseError(response, payload));
        return payload;
      })
      .then(payload => setWorklistResponse({ key, ...payload }))
      .catch(reason => {
        if (reason.name !== "AbortError") setError("Palvelutilanteiden lataus epäonnistui.");
      });
    return () => controller.abort();
  }, [activeRole, searchQuery, worklistOffset]);

  // The day's appointments: the lens and the day are the request's own parameters. The response carries the
  // key it was asked under, so an in-flight day for a previous lens is ignored rather than shown.
  useEffect(() => {
    if (!activeRole) return undefined;
    const controller = new AbortController();
    const key = dayRequestKey(activeRole, day);
    const params = new URLSearchParams({ role: activeRole, day });
    fetch(`${API}/api/worklist?${params}`, { signal: controller.signal })
      .then(async response => {
        const payload = await response.json();
        if (!response.ok) throw new Error(responseError(response, payload));
        return payload;
      })
      .then(payload => setDayResponse({ key, ...payload }))
      // Silent by design: a day that cannot be read leaves the role's worklist standing in (see DayView), and
      // the briefing's own error line is not replaced by a navigation failure. The key is still recorded, so the
      // view stops saying it is loading.
      .catch(reason => {
        if (reason.name !== "AbortError") setDayResponse({ key, ...EMPTY_DAY });
      });
    return () => controller.abort();
  }, [activeRole, day]);

  // The library selects dark with `:root[data-theme="dark"]`, so the attribute belongs on <html> — it
  // also drives `color-scheme` and the dark `--color-bg` swap, neither of which a div can reach.
  // index.html sets it before first paint from the same URL; this keeps it in step when the control
  // changes without a reload.
  useEffect(() => {
    document.documentElement.dataset.theme = pageOptions.theme;
  }, [pageOptions.theme]);

  useEffect(() => {
    if (!encounterId) return undefined;
    let ignored = false;
    let deadlineExpired = false;
    const controller = new AbortController();
    const deadline = window.setTimeout(() => {
      deadlineExpired = true;
      controller.abort();
    }, REQUEST_DEADLINE_MS);
    const pictureUrl = `${API}/api/encounters/${encodeURIComponent(encounterId)}/picture`
      + (mode === "normal" ? "" : `?mode=${encodeURIComponent(mode)}`);
    fetch(pictureUrl, {
      signal: controller.signal,
    })
      .then(async response => {
        const payload = await response.json();
        if (!response.ok) throw new Error(responseError(response, payload));
        return payload;
      })
      .then(result => {
        if (!ignored) setPicture(result);
      })
      .catch(reason => {
        if (ignored || reason.name === "AbortError") {
          if (!ignored && deadlineExpired) setError("Tilannekuvan valmistelu ylitti 180 sekunnin aikarajan.");
          return;
        }
        setError(reason.message || "Tilannekuvan lataus epäonnistui.");
      })
      .finally(() => {
        window.clearTimeout(deadline);
        if (!ignored) setLoading(false);
      });

    return () => {
      ignored = true;
      window.clearTimeout(deadline);
      controller.abort();
    };
  }, [encounterId, mode, pictureAttempt]);

  function retryPrefill() {
    setLoading(true);
    setError("");
    setPictureAttempt(attempt => attempt + 1);
  }

  // What this professional already decided on this encounter, so a reload renders a decided proposal as
  // decided instead of offering the same three buttons again. It is a separate route from the Picture on
  // purpose: the Picture is a pure function of `(encounter, mode)` that is compared between arms and
  // captured as an artifact, while this answer depends on who is asking. It is keyed on the encounter and
  // not on `mode`, because switching the presenter's mode does not change what was decided.
  //
  // A membership failure here is not a briefing failure: the panel falls back to offering the decisions,
  // which is exactly the pre-reload behaviour, so it stays silent rather than replacing the picture's own
  // error. The generation guard is the same one every other response uses, so a late answer for a previous
  // encounter cannot seed the current one.
  //
  // This is the ONE request for the route per encounter open. The assistant rail used to make its own
  // (C2), so one open was two identical requests; it now takes the payload as the `decisions` prop below.
  useEffect(() => {
    if (!encounterId) return undefined;
    const generation = contextGeneration.current;
    const controller = new AbortController();
    fetch(`${API}/api/encounters/${encodeURIComponent(encounterId)}/decisions`, { signal: controller.signal })
      .then(async response => (response.ok ? response.json() : null))
      .then(payload => {
        if (!payload || !feedbackContextIsCurrent(generation, contextGeneration.current)) return;
        setDecisions(payload);
        setReviewRequests(current => seedProposalReviews(current, payload));
      })
      .catch(() => {});
    return () => controller.abort();
  }, [encounterId]);

  // The same, for the pre-filled form's verdicts: one read per open, scoped by the route to this caller, and
  // silent on failure for the same reason — the form then offers its verdicts, which is the pre-reload behaviour.
  useEffect(() => {
    if (!encounterId) return undefined;
    const generation = contextGeneration.current;
    const controller = new AbortController();
    fetch(`${API}/api/encounters/${encodeURIComponent(encounterId)}/prefill/reviews`, { signal: controller.signal })
      .then(async response => (response.ok ? response.json() : null))
      .then(payload => {
        if (!payload || !feedbackContextIsCurrent(generation, contextGeneration.current)) return;
        setPrefillReviews(current => seedPrefillReviews(current, payload));
      })
      .catch(() => {});
    return () => controller.abort();
  }, [encounterId]);

  // The presenter's mode switch (Gate 3). `mode` is the picture route's own parameter, so this recomputes
  // *this* encounter rather than describing a different one. `normal` is the absence of the parameter; the
  // address builder enforces that, so a URL never records `mode=normal` as a choice.
  // The page the address has to keep naming when a toggle rewrites it: the technical page, or nothing (the
  // briefing and the day are the absence of the parameter).
  const page = destination === "technical" ? "technical" : undefined;

  function selectMode(next) {
    if (next === mode) return;
    setMode(next);
    // The role written is the lens on screen (`activeRole`), not the raw picker choice, which is empty when
    // the role was inferred from the open case; writing the raw choice would drop the lens from the address.
    writeAddress({ encounterId, role: activeRole, mode: next, theme: pageOptions.theme, capture: pageOptions.capture, page },
      { replace: true });
  }

  function selectTheme(theme) {
    // Both halves of the swap are the transition: the state that the effect below writes to <html>, and
    // the attribute write itself, which only the pre-paint script in index.html otherwise performs — so
    // the colour change and the `color-scheme` change (and with it the native scrollbars) are one paint.
    startViewTransition(() => {
      document.documentElement.dataset.theme = theme;
      setPageOptions(current => ({ ...current, theme }));
    });
    try { window.localStorage?.setItem("luvn-theme", theme); } catch { /* storage may be unavailable */ }
    // The effective role, for the same reason as `selectMode`: the address names the lens that is on screen.
    writeAddress({ encounterId, role: activeRole, mode, theme, capture: pageOptions.capture, page }, { replace: true });
  }

  // Rooli is a first-class axis: choosing one changes which worklist is shown, not what any encounter
  // discloses. Every opened case still runs `policy.cut` on the server, so this selection is a lens.
  function selectRole(role) {
    contextGeneration.current += 1;
    setRoleChoice(role);
    setEncounterId("");
    setUnknownEncounter(false);
    setPicture(null);
    setLoading(false);
    setError("");
    setReviewRequests({});
    setPrefillReviews({});
    setDecisions(null);
    setFailureComparison(null);
    setComparisonError("");
    setDestination("day");
    // A new lens starts at its first page; a stale offset would show an empty worklist.
    setWorklistOffset(0);
    writeAddress({ role, mode, theme: pageOptions.theme, capture: pageOptions.capture });
  }

  // The day picked on the strip. It is part of the address, so Back returns to the previous day.
  function selectDay(next) {
    if (!next || next === day) return;
    setDay(next);
    setDestination("day");
    writeAddress({ role: activeRole, mode, theme: pageOptions.theme, capture: pageOptions.capture, day: next });
  }

  // The technical page: a destination, pushed to the address as `page=technical` so Back returns to the
  // briefing and a reload reopens the page. It describes the open encounter's Picture, so the address keeps
  // naming that encounter and its lens.
  function openTechnical() {
    setDestination("technical");
    writeAddress({ encounterId, role: activeRole, mode, theme: pageOptions.theme, capture: pageOptions.capture, page: "technical" });
  }

  // Tilannekuva from the rail. Leaving the technical page writes the address without `page`, so the address
  // and the view agree; from the day the briefing shows the loaded case as before, without naming it.
  function openBriefing() {
    if (destination === "technical") {
      writeAddress({ encounterId, role: activeRole, mode, theme: pageOptions.theme, capture: pageOptions.capture });
    }
    setDestination("briefing");
  }

  // Open or close the assistant pane. The address is rewritten in place (`replace`): opening the pane is not a
  // navigation, and Back should return to the previous view rather than to the pane's closed state. The fields
  // are the view on screen, exactly as `selectTheme` writes them, except that the day view keeps naming its day
  // and not the case loaded behind it.
  function setAssistant(next, opener = null) {
    if (next === assistantOpen) return;
    if (next) assistantOpener.current = opener;
    assistantFocusDue.current = next ? "pane" : "opener";
    setAssistantOpen(next);
    const common = { role: activeRole, mode, theme: pageOptions.theme, capture: pageOptions.capture, assistant: next };
    writeAddress(destination === "day" ? { ...common, day } : { ...common, encounterId, page }, { replace: true });
  }

  // The focus moves the toggle owes, after the pane has rendered: into the pane (its heading) on open,
  // back to the control that opened it on close — or to the header toggle when the opener is gone (a phone
  // toggle that left the DOM at a wider width) or the pane was opened by the address.
  useEffect(() => {
    const due = assistantFocusDue.current;
    assistantFocusDue.current = null;
    if (due === "pane" && assistantOpen) assistantHeading.current?.focus();
    else if (due === "opener" && !assistantOpen) {
      const opener = assistantOpener.current;
      (opener?.isConnected ? opener : assistantHeaderToggle.current)?.focus();
    }
  }, [assistantOpen]);

  async function runFailureComparison() {
    const generation = contextGeneration.current;
    setComparisonLoading(true);
    setComparisonError("");
    try {
      const results = await Promise.all(["decision-off", "foundry-off"].map(async mode => {
        const response = await fetch(`${API}/api/encounters/${encodeURIComponent(encounterId)}/picture?mode=${mode}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(responseError(response, payload));
        return payload;
      }));
      if (generation === contextGeneration.current) setFailureComparison({ decisionOff: results[0], foundryOff: results[1] });
    } catch (reason) {
      if (generation === contextGeneration.current) setComparisonError(reason.message || "Vertailutilojen valmistelu epäonnistui.");
    } finally {
      if (generation === contextGeneration.current) setComparisonLoading(false);
    }
  }

  function updateReview(proposalId, update, generation) {
    if (!feedbackContextIsCurrent(generation, contextGeneration.current)) return;
    setReviewRequests(current => ({ ...current, [proposalId]: update }));
  }

  function transitionReview(event) {
    const transition = reviewWriteReducer(pendingReviewEvents.current, event);
    pendingReviewEvents.current = transition.pending;
    return transition.effect;
  }

  async function postReview(proposal, effect) {
    const { scope, body, bodyKey, eventId } = effect;
    const key = proposal.proposal_id;
    const generation = contextGeneration.current;
    updateReview(key, { status: effect.status, body, eventId }, generation);
    try {
      const response = await fetch(`${API}/api/encounters/${encodeURIComponent(encounterId)}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, event_id: eventId }),
      });
      const payload = await response.json();
      if (!response.ok) {
        const error = new Error(responseError(response, payload));
        error.status = response.status;
        // The body is carried on the failure so the 409 that means "you already decided this" can be told
        // from the other 409s (an unavailable proposal, a conflicting id) by its own discriminator.
        error.payload = payload;
        throw error;
      }
      transitionReview({ type: "success", scope, bodyKey });
      updateReview(key, { status: "saved", action: body.action, body, eventId, recordedAt: payload.recorded_at }, generation);
      return true;
    } catch (reason) {
      const outcome = transitionReview({
        type: "failure", scope, bodyKey, status: reason.status,
        alreadyDecided: decisionAlreadyRecorded(reason.status, reason.payload),
      });
      updateReview(key, {
        status: outcome.status,
        action: body.action,
        body,
        eventId,
        error: reason.message || "Palautteen tallennus epäonnistui.",
      }, generation);
      return false;
    }
  }

  function submitReview(proposal, action, note) {
    const body = { proposal_id: proposal.proposal_id, rule_version: proposal.rule_version, action };
    if (action === "edit") body.note = note;
    const scope = feedbackScopeKey(encounterId, proposal.proposal_id);
    const effect = transitionReview({ type: "submit", scope, body, candidateEventId: newEventId() });
    if (effect.type === "blocked") {
      updateReview(proposal.proposal_id, {
        status: effect.status,
        body: effect.body,
        eventId: effect.eventId,
        error: "Aiemman tallennuksen tila on epäselvä. Yritä sama tallennus uudelleen samalla tapahtumatunnisteella ennen muutosta.",
      }, contextGeneration.current);
      return Promise.resolve(false);
    }
    return effect.type === "post" ? postReview(proposal, effect) : Promise.resolve(false);
  }

  function retryReview(proposal) {
    const scope = feedbackScopeKey(encounterId, proposal.proposal_id);
    const effect = transitionReview({ type: "retry", scope, previous: reviewRequests[proposal.proposal_id] });
    return effect.type === "post" ? postReview(proposal, effect) : Promise.resolve(false);
  }

  // The pre-filled form's verdicts go through the same reducer as the proposal decisions: one id per body, held
  // across an ambiguous outcome so a retry is the same write. The id the reducer issues is the route's
  // `review_id`, so a retry under it is idempotent and a different body under it is the 409 the route refuses.
  function updatePrefillReview(key, update, generation) {
    if (!feedbackContextIsCurrent(generation, contextGeneration.current)) return;
    setPrefillReviews(current => ({ ...current, [key]: update }));
  }

  async function postPrefillReview(form, item, effect) {
    const { scope, body, bodyKey, eventId } = effect;
    const key = prefillReviewKey(form.form_id, item.question_id);
    const generation = contextGeneration.current;
    updatePrefillReview(key, { status: effect.status, verdict: body.verdict, editedText: body.edited_text ?? null, body, eventId }, generation);
    try {
      const response = await fetch(`${API}/api/encounters/${encodeURIComponent(encounterId)}/prefill/reviews`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, review_id: eventId }),
      });
      const payload = await response.json();
      if (!response.ok) {
        const error = new Error(responseError(response, payload));
        error.status = response.status;
        throw error;
      }
      transitionReview({ type: "success", scope, bodyKey });
      updatePrefillReview(key, { status: "saved", verdict: body.verdict, editedText: payload.edited_text ?? body.edited_text ?? null,
        reviewId: eventId, body, eventId, recordedAt: payload.recorded_at }, generation);
      return true;
    } catch (reason) {
      // The route's one 409 is a conflicting retry under an id it already holds: the verdict is on record, so the
      // rail says so rather than offering it again.
      const outcome = transitionReview({ type: "failure", scope, bodyKey, status: reason.status, alreadyDecided: reason.status === 409 });
      updatePrefillReview(key, { status: outcome.status, verdict: body.verdict, editedText: body.edited_text ?? null, body, eventId,
        error: reason.message || "Vastauksen tallennus epäonnistui." }, generation);
      return false;
    }
  }

  function submitPrefillReview(form, item, verdict, editedText) {
    const body = { form_id: form.form_id, question_id: item.question_id, verdict };
    if (verdict === "muokattu") body.edited_text = editedText;
    const scope = prefillReviewScopeKey(encounterId, form.form_id, item.question_id);
    const effect = transitionReview({ type: "submit", scope, body, candidateEventId: newEventId() });
    if (effect.type === "blocked") {
      updatePrefillReview(prefillReviewKey(form.form_id, item.question_id), {
        status: effect.status, verdict: effect.body.verdict, editedText: effect.body.edited_text ?? null,
        body: effect.body, eventId: effect.eventId,
        error: "Aiemman tallennuksen tila on epäselvä. Yritä sama tallennus uudelleen samalla tapahtumatunnisteella ennen muutosta.",
      }, contextGeneration.current);
      return Promise.resolve(false);
    }
    return effect.type === "post" ? postPrefillReview(form, item, effect) : Promise.resolve(false);
  }

  function retryPrefillReview(form, item) {
    const scope = prefillReviewScopeKey(encounterId, form.form_id, item.question_id);
    const effect = transitionReview({ type: "retry", scope, previous: prefillReviews[prefillReviewKey(form.form_id, item.question_id)] });
    return effect.type === "post" ? postPrefillReview(form, item, effect) : Promise.resolve(false);
  }

  // The worklist rows for the current server page, mapped through the same role scoping as before. The
  // role and the search are the request's own parameters, so this is the panel's whole content — there is
  // no second, client-side filter over a list the browser fetched in full. The page is only the response
  // that matches the current lens, search and offset; a stale one renders as nothing, not as the next
  // lens's worklist.
  const worklistPage = worklistResponse.key === worklistRequestKey(activeRole, searchQuery, worklistOffset)
    ? worklistResponse : EMPTY_WORKLIST;
  // The page for the current request has not arrived yet. The worklist says it is loading rather than
  // reporting "no encounters", which is a different statement and would be false while a page is in flight.
  const worklistPending = Boolean(activeRole)
    && worklistResponse.key !== worklistRequestKey(activeRole, searchQuery, worklistOffset);
  const pickerEncounters = activeRole ? worklistFor(worklistPage.encounters, activeRole) : [];
  const dayPage = dayResponse.key === dayRequestKey(activeRole, day) ? dayResponse : EMPTY_DAY;
  const dayPending = Boolean(activeRole) && dayResponse.key !== dayRequestKey(activeRole, day);
  // Reuse same-person picker metadata to name sibling synthetic lenses; their Pictures are never loaded here.
  const lensSiblings = selected
    ? encounters.filter(item => item.example_label === selected.example_label
      && item.encounter_id !== selected.encounter_id)
    : [];

  const currentReviews = reviewRequests;
  const identityAvailable = Boolean(picture?.identity_header
    && picture.identity_header !== "Henkilöllisyystieto ei saatavilla");
  const identityHeader = identityAvailable ? picture.identity_header : "Henkilöllisyystieto ei saatavilla";
  // The record anchors the briefing actually renders, so an offered question's evidence links only where
  // that `#source-<id>` element exists; `SourceLink` renders plain text otherwise rather than a dead link.
  // `picturePresentation` is the same adapter the briefing renders from, so the anchor set cannot drift.
  const chatRecordTargets = picture ? new Set(picturePresentation(picture).targetRecordIds) : undefined;

  // The destinations, in the library's NavItem, for the sidebar and the phone bottom bar. One description
  // of the navigation is what stops the copies drifting. The professional's own destinations are
  // `primaryDestinations`, and the only ones in the bottom bar. `utilityDestinations` are reachable from
  // the rail under their own landmark, and are not part of the professional's path.
  // Choosing the day destination writes the address (the role and the day), so a reload returns to it.
  function openDay() {
    setDestination("day");
    writeAddress({ role: activeRole, mode, theme: pageOptions.theme, capture: pageOptions.capture, day });
  }
  const primaryDestinations = [
    { id: "day", href: "#brief-content", label: "Työpäivä", icon: <CalendarDays strokeWidth={1.5} />,
      onSelect: openDay },
    { id: "briefing", href: "#brief-content", label: "Tilannekuva", icon: <FileText strokeWidth={1.5} />,
      onSelect: openBriefing },
  ];
  const utilityDestinations = [
    // A destination like the two above: the technical page renders in the content landmark, which is what
    // the href promises, and the click is intercepted to push the page's own address.
    { id: "technical", href: "#brief-content", label: "Tekniset tiedot", icon: <Terminal strokeWidth={1.5} />,
      onSelect: openTechnical },
  ];

  // ONE h1 PER VIEW, owned by the library's Header. `Header` renders the page title as the page's heading;
  // the briefing's own heading is switched off with `hideHeading` below and the worklist panel's is an h2,
  // so neither route can show two. The title names the destination, and every destination has its own.
  // (This comment deliberately never writes the h1 tag: the design-system gate counts literal h1 tags in
  // this file, and a tag written in a comment counts as one.)
  const pageTitle = { day: "Työpäivä", technical: "Tekniset tiedot" }[destination] ?? "Tilannekuva";
  // The two destinations that show one encounter: they share the loading and empty states below.
  const onEncounter = destination === "briefing" || destination === "technical";
  const themeIsDark = pageOptions.theme === "dark";
  // The library's Search owns the accessible name. The rule: keyboard focus alone must not change the
  // destination — the input is the last focusable element before `main`, so switching on focus removed the
  // briefing's own interactive elements from the tab order (issue 10). A pointer press still opens the
  // worklist (a click focuses the field), and typing does too.
  const searchFieldProps = {
    label: "Hae palvelutilannetta",
    placeholder: "Hae palvelutilannetta",
    name: "q",
    id: "brief-search",
    value: pickerQuery,
    onPointerDown: () => setDestination("day"),
    onChange: event => {
      setPickerQuery(event.target.value);
      // A new search starts the server-side page walk again rather than staying on a stale offset.
      setWorklistOffset(0);
      setDestination("day");
    },
  };
  // One NavItem per destination per surface. No `layout`: BottomNav sets `bottom-bar` on its own children.
  // The sidebar nav and the bottom bar are never on screen together, but jsdom applies no media query, so
  // each landmark carries its own name (axe `landmark-unique`).
  const destinationNavItem = (navLabel, item) => <NavItem key={`${navLabel}-${item.id}`} href={item.href}
    icon={item.icon} selected={destination === item.id}
    onClick={event => { event.preventDefault(); item.onSelect(); }}>{item.label}</NavItem>;

  // The assistant toggle, once in the header beside the search and once in the phone tools row (the header's
  // copy is there at every width; the phone row repeats it beside the lens and the theme, where a phone's
  // thumb is). `aria-expanded` and `aria-controls` tie each copy to the one pane.
  const assistantToggle = ref => <IconButton ref={ref} icon={<Sparkles />}
    label={assistantOpen ? "Sulje avustaja" : "Avaa avustaja"}
    aria-expanded={assistantOpen} aria-controls={ASSISTANT_PANE_ID}
    onClick={event => setAssistant(!assistantOpen, event.currentTarget)} />;
  // F1: the key makes React unmount and remount the panel when the encounter changes, so the previous
  // subject's turn cannot render even for the frame before ChatPanel's own reset effect runs. The component
  // still owns the reset; this closes the one-frame gap. The panel also gets the anchor set, so an offered
  // question links its evidence only where the briefing renders it, and the decisions this shell already
  // loaded, so it asks the route for nothing. The pane's own header carries the scope chip, so the panel's
  // identity row and its chip are switched off rather than printed twice. The panel mounts once the Picture
  // has landed, as it did inside the briefing: while the briefing is being prepared the shell asks for
  // nothing but the catalogue, the Picture, the decisions and the pre-fill verdicts.
  const assistantChat = encounterId && selected && picture
    ? <ChatPanel key={encounterId} encounterId={encounterId} api={API} recordTargets={chatRecordTargets}
      decisions={decisions} identity={false} />
    : null;

  // The theme lives on <html> (see the effect above); `data-style`, `data-preset` and `data-mode` are gone
  // with the seven-palette skin, and `data-mode` was read by no selector or test. The control mirrors the
  // reference header: a labelled icon button, not a select — one appearance state, one toggle.
  return <div className={assistantOpen ? "brief-app brief-assistant-open" : "brief-app"}
    data-capture={pageOptions.capture ? "1" : undefined}>
    <a className="brief-skip" href="#brief-content">Siirry sisältöön</a>
    <AppShell
      sidebar={<div className="brief-sidebar" role="region" aria-label="Sivupalkki">
        <Sidebar productName="LUVN" navLabel="Päänavigaatio">
          {primaryDestinations.map(item => destinationNavItem("rail", item))}
        </Sidebar>
        {/* A sibling of the Sidebar, not a child: the library's Sidebar wraps its children in its own nav. */}
        <nav className="brief-utility-nav" aria-label="Työkalut">
          {utilityDestinations.map(item => destinationNavItem("rail-utility", item))}
        </nav>
        <RolePicker roster={roster} role={activeRole} onSelect={selectRole} />
        <IconButton label={themeIsDark ? "Vaihda vaaleaan teemaan" : "Vaihda tummaan teemaan"}
          icon={themeIsDark ? <Sun /> : <Moon />}
          onClick={() => selectTheme(themeIsDark ? "light" : "dark")} />
      </div>}
      header={/* On a phone the breadcrumb and the role are left out (C3): the library Header's full anatomy is
          taller than two rows at 390 px, and the role is on the Rooli control in the phone tools row anyway. */
        <Header title={pageTitle}
        breadcrumb={phone ? undefined
          : <Breadcrumb items={[{ label: "Työtila", href: "#brief-content" }]} current={pageTitle} />}
        search={/* The vendored Search is a bare label; the landmark around it is the app's. The assistant
            toggle shares the Header's search slot, so the two sit on one row at every width. */
          <span className="brief-header-tools">
            <span role="search"><Search {...searchFieldProps} /></span>
            {assistantToggle(assistantHeaderToggle)}
          </span>}
        user={{ name: "Ammattilaisdemo", initials: "D", role: phone ? undefined : (activeRoleLabel || undefined) }} />}
      bottomNav={<BottomNav label="Pikavalikko">
        {primaryDestinations.map(item => destinationNavItem("bottom", item))}
      </BottomNav>}>
      {/* The sidebar is hidden on phones, so the lens and the theme live here too (CSS). */}
      <div className="brief-phone-tools">
        <RolePicker roster={roster} role={activeRole} onSelect={selectRole} id="brief-role-picker-mobile"
          className="brief-role-picker-inline" />
        <IconButton label={themeIsDark ? "Vaihda vaaleaan teemaan" : "Vaihda tummaan teemaan"}
          icon={themeIsDark ? <Sun /> : <Moon />}
          onClick={() => selectTheme(themeIsDark ? "light" : "dark")} />
        {assistantToggle()}
      </div>
        {/* Transient shell state, tagged so capture mode can hide exactly these and nothing else: the
            previous rule hid every `[role="status"]`/`[role="alert"]` under `[data-capture]`, which is also
            what the library's Alert renders, so a captured briefing silently lost its alerts panel and axe
            skipped the hidden content (issue 07). */}
        {loading && onEncounter && <p role="status" className="t-body muted brief-app-status brief-transient-status">Esitiedot valmistellaan…</p>}
        {error && <p role="alert" className="t-caption brief-inline-status brief-app-status brief-transient-status">{error}</p>}
      <div id="brief-content" className="brief-main">
          {destination === "day" && <DayView day={day} days={dayPage.days} rows={dayPage.rows} ready={dayPage.ready}
            warmup={dayPage.warmup}
            pending={dayPending} roleLabel={activeRoleLabel} onSelectDay={selectDay} onOpen={openEncounter}
            selectedId={encounterId}
            search={searchQuery ? { query: searchQuery, items: pickerEncounters, summary: worklistPage.matching,
              total: worklistPage.total, offset: worklistOffset, pending: worklistPending, onPage: setWorklistOffset } : null}
            fallback={{ items: pickerEncounters, summary: worklistPage.matching, total: worklistPage.total,
              offset: worklistOffset, pending: worklistPending, onPage: setWorklistOffset }} />}
          {onEncounter && loading && !picture && selected && <PictureLoading key={encounterId} presentation={{
            roleLabel: selected.role_label || activeRoleLabel, purposeLabel: selected.purpose_label }} />}
          {destination === "briefing" && picture && selected && <PictureBriefing
            picture={picture}
            mode={mode}
            personName={identityHeader}
            exampleLabel={selected.example_label}
            identityAvailable={identityAvailable}
            roleLabel={selected.role_label}
            purposeLabel={selected.purpose_label}
            hideHeading
            lensSiblings={lensSiblings}
            onOpenLens={openEncounter}
            proposalReviews={currentReviews}
            onProposalReview={submitReview}
            onRetryProposalReview={retryReview}
            prefillReviews={prefillReviews}
            onPrefillReview={submitPrefillReview}
            onRetryPrefillReview={retryPrefillReview}
            onRetryPrefill={retryPrefill}
            prefillRetrying={loading}
            onOpenTechnical={openTechnical}
          />}
          {/* The technical page renders from the same Picture and the same catalogue row as the briefing. */}
          {destination === "technical" && picture && selected && <TechnicalView
            picture={picture}
            mode={mode}
            onModeChange={selectMode}
            personName={identityHeader}
            exampleLabel={selected.example_label}
            identityAvailable={identityAvailable}
            roleLabel={selected.role_label}
            purposeLabel={selected.purpose_label}
            failureComparison={failureComparison}
            comparisonLoading={comparisonLoading}
            comparisonError={comparisonError}
            onRunFailureComparison={runFailureComparison}
            lensSiblings={lensSiblings}
          />}

          {onEncounter && !loading && !error && !picture && !unknownEncounter
            && <p className="t-caption brief-empty-panel">Ei valittua palvelutilannetta. Valitse tapaus omalta työlistalta.</p>}
          {onEncounter && !loading && !picture && unknownEncounter
            && <p className="t-caption brief-empty-panel" role="status">Valittua palvelutilannetta ei ole tässä aineistossa. Valitse tapaus omalta työlistalta.</p>}
      </div>
    </AppShell>
    {/* The assistant pane is a sibling of the shell, not a child of `main`: a complementary landmark inside
        the main landmark is what axe (and a screen reader's landmark list) would report, and the grid on
        `.brief-app` is what docks it beside the shell. Escape closes it from anywhere inside the pane only;
        the search field's own Escape (clear the text) is not hijacked. */}
    <AssistantPane open={assistantOpen} onClose={() => setAssistant(false)} headingRef={assistantHeading}
      onKeyDown={event => {
        if (event.key !== "Escape" || event.defaultPrevented) return;
        event.preventDefault();
        setAssistant(false);
      }}
      roleLabel={selected ? (selected.role_label || activeRoleLabel) : ""}
      personLabel={selected && identityAvailable ? identityHeader : ""}
      chat={assistantChat} />
  </div>;
}

// Whether an address asks for the assistant pane. `assistant=1` is the only spelling written; any present value
// is read as open, as `capture` is.
function assistantAddressed(search) {
  return new URLSearchParams(search ?? "").has("assistant");
}

// The loading panel for a briefing being prepared. It makes no identity claim: the catalogue's `example_label`
// is the worklist's demo label, not a sourced identity, so the Henkilö row stays out until the Picture supplies
// one. The role and the purpose come from the catalogue row. The block is the library's loading state: its
// placeholder bars wait 300 ms, the block is `aria-busy`, and the wrapper is `aria-busy` too so the context strip
// is read as part of the work in progress; the status line in the shell is what says the work is happening.
// The label is passed, never defaulted: the library's own is English. `key` on the caller restarts the wait
// per encounter.
function PictureLoading({ presentation }) {
  return <div aria-busy="true">
    <EncounterContext presentation={presentation} />
    <BlockState kind="loading" title="Esitiedot" loadingLabel="Esitiedot valmistellaan…" loadingDelay={300} />
  </div>;
}

// NEW: the Rooli selector. It renders only when the catalogue actually carries a professional role, so
// it never offers an axis the API did not return; with no roster it is one caption line and no control.
// `id`/`className` are parameters because the control renders twice — in the rail and, because the
// rail is hidden on phones, in the phone tools above the content. The id must differ between them or the
// document carries two elements with one id and the label binds to the first of them.
export function RolePicker({ roster, role, onSelect, id = "brief-role-picker", className = "" }) {
  const classes = ["brief-role-picker", className].filter(Boolean).join(" ");
  if (roster.length === 0) {
    return <div className={classes}><p className="t-caption brief-role-note">Ei palvelutilanteita.</p></div>;
  }
  return <div className={classes}>
    <label className="t-caption" htmlFor={id}>Ammattirooli
      <select id={id} className="t-control" value={role} onChange={event => onSelect(event.target.value)}>
        {roster.map(entry => <option value={entry.role} key={entry.role}>{entry.label}</option>)}
      </select>
    </label>
    <p className="t-caption brief-role-note">Työlista rajautuu valittuun rooliin.</p>
  </div>;
}

export { CasePicker } from "./DayView.jsx";
