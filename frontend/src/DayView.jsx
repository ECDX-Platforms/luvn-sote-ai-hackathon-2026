import { Button, Metric, MetricRow, StatusTag, Tag } from "../vendor/comp-lib/src/index.ts";
import { attentionChips } from "./worklist.mjs";
import { formatDecisionTime } from "./challenge-demo.mjs";

const WORKLIST_PAGE_SIZE = 25;

/**
 * Työpäivä: the professional's day, from the release's own appointment tables (`GET /api/worklist`).
 *
 * Every row is one scheduled occasion that opens one briefing. What the row shows before the click comes from
 * that briefing's cached attention summary and from the appointment's own source fields: the source's own
 * urgency wording, the cached risk level, the conflicts the briefing found, the running medications it lists,
 * and the record Decision-1 ranked most urgent. Every number is a count of rows the person's own briefing
 * renders (CONTEXT.md: no derived number may require a withheld denominator). A row whose briefing is not
 * cached yet says so (`Valmistellaan`) rather than guessing.
 *
 * The release carries dates, not clock times, so no time is shown and none is invented; the rows are in the
 * server's order (urgent contact first, then risk, then id).
 */

const WEEKDAYS = ["su", "ma", "ti", "ke", "to", "pe", "la"];

function dayParts(iso) {
  const parsed = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) return { weekday: "", day: iso, long: iso };
  return {
    weekday: WEEKDAYS[parsed.getUTCDay()],
    day: String(parsed.getUTCDate()),
    long: new Intl.DateTimeFormat("fi-FI", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(parsed),
  };
}

function urgentContact(row) {
  return typeof row.urgency_text === "string" && /^hoito akuutti/i.test(row.urgency_text);
}

export function DayView({ day, days = [], rows = [], ready = 0, pending = false, roleLabel = "", onSelectDay, onOpen,
  selectedId, search = null, fallback = null, warmup }) {
  const { long } = dayParts(day);
  const searching = Boolean(search?.query);
  // A corpus with no appointment table (the fixture and demo corpora) has no day to show: the role's own
  // worklist stands in, named for what it is, so navigation never depends on a table the corpus may lack.
  const standIn = !searching && !pending && rows.length === 0 && fallback && fallback.items?.length > 0;
  return <section className="brief-day" aria-labelledby="brief-day-heading">
    <header>
      <p className="t-overline brief-eyebrow">Työpäivä{roleLabel ? ` · ${roleLabel}` : ""}</p>
      <h2 className="t-title-lg" id="brief-day-heading">{long}</h2>
      {!searching && <p className="t-caption">
        {pending ? "Päivän vastaanottoja ladataan…"
          : rows.length === 0 ? "Ei vastaanottoja tälle päivälle."
            : `${rows.length} vastaanottoa · ${ready} esitietoa valmiina`}
      </p>}
    </header>
    {days.length > 0 && !searching && <nav className="brief-day-strip" aria-label="Päivät">
      {days.map(entry => {
        const parts = dayParts(entry.day);
        const current = entry.day === day;
        return <button type="button" key={entry.day} className="brief-day-pick" aria-pressed={current}
          onClick={() => onSelectDay?.(entry.day)}>
          <span className="t-caption">{parts.weekday}</span>
          <span className="t-body-strong">{parts.day}</span>
          <span className="t-caption">{entry.appointments > 0 ? entry.appointments : "–"}</span>
        </button>;
      })}
    </nav>}
    {/* When the briefings cache was last written (`warmup.last_built_at`): a timestamp, not a count, and the
        honest answer to "is the day prepared?". It renders only once the day's response has arrived; `null`
        from the server means nothing has been prepared yet, which is said in words rather than as a date. */}
    {!searching && warmup !== undefined && <p className="t-caption brief-day-warmup">
      {warmup?.last_built_at ? `Valmisteltu viimeksi ${formatDecisionTime(warmup.last_built_at)}` : "Esitietoja ei ole vielä valmisteltu"}
    </p>}
    {searching ? <section className="brief-day-search" aria-label="Hakutulokset">
      <CasePicker heading="Hakutulokset" eyebrow="Haku" items={search.items} summary={search.summary} total={search.total}
        offset={search.offset} pending={search.pending} onPage={search.onPage} selectedId={selectedId} onSelect={onOpen} />
    </section> : <>
      <ol className="brief-day-rows" aria-label="Päivän vastaanotot">
        {rows.map(row => {
          const chips = attentionChips(row.attention);
          const readyNow = row.readiness === "ready";
          return <li key={row.appointment_id}>
            <button type="button" className="brief-day-row" aria-pressed={row.encounter_id === selectedId}
              onClick={() => onOpen?.(row.encounter_id)}>
              <span className="brief-day-row-main">
                <span className="t-body-strong">{row.example_label || row.encounter_id}</span>
                <span className="t-caption">{[row.purpose_label, row.role_label].filter(Boolean).join(" · ")}</span>
                {row.attention?.lead && <span className="t-caption brief-day-lead">
                  Ensin: {row.attention.lead.title} — {row.attention.lead.urgency_text}
                </span>}
              </span>
              <span className="brief-day-row-side">
                {urgentContact(row) && <StatusTag status="warning">Kiireellinen (lähteen mukaan)</StatusTag>}
                {chips.map(chip => chip.status
                  ? <StatusTag key={chip.key} status={chip.status}>{chip.text}</StatusTag>
                  : <Tag key={chip.key}>{chip.text}</Tag>)}
                <Tag className={readyNow ? "brief-ready" : "brief-pending"}>{readyNow ? "Esitiedot valmiina" : "Valmistellaan"}</Tag>
              </span>
            </button>
          </li>;
        })}
      </ol>
      {!standIn && <p className="t-caption brief-day-note">Vastaanottopäivät ovat aineiston omia; kellonaikaa aineistossa ei ole, eikä sitä keksitä. Rivin merkinnät ovat henkilön oman esitietokoosteen lukuja.</p>}
      {standIn && <section className="brief-day-search" aria-label="Palvelutilanteet">
        <p className="t-caption brief-day-note">Tälle päivälle ei ole vastaanottoja aineistossa; alla roolin palvelutilanteet.</p>
        <CasePicker heading="Palvelutilanteet" items={fallback.items} summary={fallback.summary} total={fallback.total}
          offset={fallback.offset} pending={fallback.pending} onPage={fallback.onPage} selectedId={selectedId} onSelect={onOpen} />
      </section>}
    </>}
  </section>;
}

// The role-scoped worklist. It replaces the flat corpus list: a professional sees their own role's
// Kohtaamiset, not every encounter in the demo. Every row is a navigation entry over a catalogue the API
// already returned; no count here includes a withheld record, and a row only exists for the active role.
// The optional time mark is an authored synthetic value and is labelled as one wherever it renders.
// It takes already-scoped worklist items, so the scoping decision cannot be undone by a caller that
// passes something wider: the component renders the rows it is handed and nothing else.
//
// The counts come from the server's `aggregate.matching`, not from a reduction over the rendered rows:
// the panel is a page, and `items` is what fits on it, so a client-side count would shrink every time the
// professional paged or searched. `matching` is the same count over the whole role and the whole search,
// computed by the endpoint that owns the catalogue. `CONTEXT.md:49` is respected by shape rather than by
// arithmetic: these are absolute counts of navigation rows, never a ratio, a percentage or a withheld
// denominator, and a count with nothing to count is an absent tile rather than a zero.
export function CasePicker({ items = [], summary = null, total = 0, offset = 0, pending = false,
  selectedId, onSelect, onPage, heading = 'Palvelutilanteet', eyebrow = 'Työtila' }) {
  const labels = [...new Set(items.map(item => item.person))];
  // The fallback is for a render with no server (the component tests): one page is then the whole list, so
  // the two sources agree. Production always supplies `summary`.
  const counts = summary ?? {
    encounters: items.length,
    people: labels.length,
    purposes: new Set(items.map(item => item.purposeLabel).filter(Boolean)).size,
  };
  const metrics = [
    counts.encounters > 0 && { id: 'encounters', value: counts.encounters, unit: 'kpl',
      label: 'Palvelutilannetta tälle roolille' },
    counts.people > 0 && { id: 'people', value: counts.people, unit: 'hlö', label: 'Henkilöä tälle roolille' },
    counts.purposes > 0 && { id: 'purposes', value: counts.purposes, unit: 'kpl', label: 'Eri palvelutarkoitusta' },
  ].filter(Boolean);
  const showsPaging = total > 0 && (offset > 0 || offset + items.length < total);
  return <section className="brief-case-picker" aria-labelledby="brief-case-picker-heading">
    <header><p className="t-overline brief-eyebrow">{eyebrow}</p>
      <h2 className="t-title-lg" id="brief-case-picker-heading">{heading}</h2>
      <p className="t-caption">Lista on navigointia, ei rekisteri-indeksi.</p></header>
    {metrics.length > 0 && <MetricRow className="brief-worklist-metrics">
      {metrics.map(metric => <Metric key={metric.id} value={metric.value} unit={metric.unit} label={metric.label} />)}
    </MetricRow>}
        {labels.map(label => {
      const cases = items.filter(item => item.person === label);
      return <section className="brief-case-group" key={label}>
        <h2 className="t-title-sm">{label}</h2>
        <div>{cases.map(item => <button type="button" key={item.encounterId}
          aria-pressed={item.encounterId === selectedId} onClick={() => onSelect(item.encounterId)}>
          <strong className="t-body-strong">{item.person} · {item.label}</strong><span className="t-caption">Avaa tilannekuva</span>
        </button>)}</div>
      </section>;
    })}
    {showsPaging && <div className="brief-button-row">
      <Button size="sm" disabled={offset === 0}
        onClick={() => onPage?.(Math.max(0, offset - WORKLIST_PAGE_SIZE))}>Edelliset</Button>
      <Button size="sm" disabled={offset + items.length >= total}
        onClick={() => onPage?.(offset + items.length)}>Seuraavat</Button>
      <p className="t-caption">Näytetään {offset + 1}–{offset + items.length} / {total}.</p>
    </div>}
    {items.length === 0 && <p className="t-caption brief-empty-panel">
      {pending ? 'Työlistaa ladataan…' : 'Ei palvelutilanteita tälle roolille.'}
    </p>}
  </section>;
}
