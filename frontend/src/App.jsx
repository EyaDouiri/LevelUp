import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, ArrowRight, Building2, Check, ChevronRight, Download, ExternalLink, Eye, EyeOff, FileText, Flame, GitBranch, Inbox, KeyRound, LoaderCircle, LogOut, Mail, MapPin, Mic, Play, Plus, RefreshCw, Save, Search, Settings2, ShieldCheck, Square, Trash2, Users, Volume2, X } from 'lucide-react'
import './App.css'

const statuses = { discovered: 'Découverte', shortlisted: 'Retenue', preparing: 'Préparation', review_needed: 'À vérifier', prepared: 'Préparée', approved: 'Validée', draft_created: 'Brouillon créé', applied: 'Envoyée', interview: 'Entretien', offer: 'Proposition', rejected: 'Refusée', archived: 'Archivée' }
const agents = [['orchestrator', 'Orchestrateur', GitBranch], ['scout', 'Éclaireur', Search], ['verifier', 'Enquêteur', ShieldCheck], ['analyst', 'Analyste', Activity], ['writer', 'Rédacteur', FileText], ['critic', 'Contrôleur', Check], ['archivist', 'Archiviste', Inbox]]
const initial = { applications: [], runs: [], events: [], companies: [], profile: {}, settings: {}, usage: {}, connections: {} }
const active = r => ['queued', 'running', 'cancelling'].includes(r.status)
const date = v => v && !Number.isNaN(Date.parse(v)) ? new Date(v).toLocaleString('fr-FR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'Non renseigné'
const url = v => { try { const u = new URL(v, window.location.origin); return ['http:', 'https:'].includes(u.protocol) ? u.href : undefined } catch { return undefined } }
const display = v => typeof v === 'string' ? v : JSON.stringify(v, null, 2)
const PASSWORD = 'wakywaky'
const SESSION_KEY = 'hxh_session'
const logonPhrases = ['Votre chasseuse de stage personnel.','Elle veille sur le pipeline 24h/24.','Dites simplement « waky waky ».','Chasser. Préparer. Conquérir.']
async function api(path, method = 'GET', body, signal) {
  const response = await fetch(`/api${path}`, { method, signal, headers: method === 'GET' ? {} : { 'Content-Type': 'application/json' }, body: method === 'GET' ? undefined : JSON.stringify(body ?? {}) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `La requête a échoué (${response.status})`)
  return data
}
function Button({ icon: Icon, children, busy, ...props }) { return <button {...props} disabled={props.disabled || busy}>{busy ? <LoaderCircle className="spin" /> : Icon && <Icon />}{children}</button> }
function Field({ label, children }) { return <label className="field"><span>{label}</span>{children}</label> }
function Empty({ title, children }) { return <div className="empty"><Inbox size={30} /><h3>{title}</h3>{children && <p>{children}</p>}</div> }
function useTypewriter(phrases, typeMs = 55, deleteMs = 26, holdMs = 1700) {
  const [text, setText] = useState('')
  const [index, setIndex] = useState(0)
  useEffect(() => {
    const phrase = phrases[index % phrases.length]
    let timer
    if (text.length < phrase.length) timer = setTimeout(() => setText(phrase.slice(0, text.length + 1)), typeMs)
    else if (text.length === phrase.length) timer = setTimeout(() => setText(phrase.slice(0, -1)), holdMs)
    else timer = setTimeout(() => setIndex(i => i + 1), deleteMs)
    return () => clearTimeout(timer)
  }, [text, index, phrases, typeMs, deleteMs, holdMs])
  return text
}
function LoginScreen({ onLogin }) {
  const phrase = useTypewriter(logonPhrases)
  const [pwd, setPwd] = useState('')
  const [show, setShow] = useState(false)
  const [shake, setShake] = useState(false)
  const submit = e => {
    e.preventDefault()
    if (pwd === PASSWORD) onLogin()
    else { setShake(true); setPwd(''); setTimeout(() => setShake(false), 420) }
  }
  return <div className="login">
    <div className="login-glow login-glow-green" />
    <div className="login-glow login-glow-orange" />
    <div className="login-embers">{Array.from({ length: 14 }, (_, i) => <i key={i} style={{ left: `${(i * 7.13 + 4) % 100}%`, animationDelay: `${(i % 7) * 0.9}s`, animationDuration: `${5.5 + (i % 5)}s` }} />)}</div>
    <main className={`login-panel ${shake ? 'login-shake' : ''}`}>
      <section className="login-side" aria-hidden="true">
        <img className="login-logo" src="/logo.svg" alt="" width="96" height="96" />
        <h1>Hunter<span className="brand-dot">×</span>Hunter</h1>
        <p className="login-tagline">{phrase}<span className="login-caret" /></p>
      </section>
      <section className="login-form">
        <span className="login-eyebrow">Espace personnel</span>
        <h2>Bon retour, chasseuse.</h2>
        <form onSubmit={submit}>
          <div className="login-form-item"><span id="login-pwd-label">Mot de passe</span><div className="login-password"><KeyRound /><input type={show ? 'text' : 'password'} value={pwd} onChange={e => setPwd(e.target.value)} autoFocus autoComplete="current-password" placeholder="waky waky ?" aria-labelledby="login-pwd-label" name="password" /></div><button type="button" className="login-eye" onClick={() => setShow(s => !s)} aria-label={show ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}>{show ? <EyeOff /> : <Eye />}</button></div>
          <button type="submit" className="primary login-submit">Entrer dans votre espace <ArrowRight /></button>
          <p className="login-hint">Accès personnel · veille privée</p>
        </form>
      </section>
    </main>
  </div>
}

let frenchVoice = null
if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
  const pickFrench = () => { frenchVoice = window.speechSynthesis.getVoices().find(v => v.lang?.toLowerCase().startsWith('fr')) || null }
  pickFrench()
  window.speechSynthesis.addEventListener?.('voiceschanged', pickFrench)
}

const voiceStates = { idle: 'Inactif', listening: 'À l’écoute', speaking: 'Parle', denied: 'Micro refusé', unsupported: 'Non supporté' }
function WakyVoice({ setError }) {
  const [voice, setVoice] = useState('idle')
  const [spoken, setSpoken] = useState('')
  const rec = useRef(null)
  const supported = typeof window !== 'undefined' && 'speechSynthesis' in window
  const say = useCallback(async () => {
    let summary
    try { summary = await api('/voice') } catch (err) { setVoice('idle'); setError(err.message); return }
    setSpoken(summary.text)
    setVoice('speaking')
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(summary.text)
    utterance.lang = frenchVoice?.lang || 'fr-FR'
    if (frenchVoice) utterance.voice = frenchVoice
    utterance.rate = 1
    utterance.onend = utterance.onerror = () => setVoice(v => v === 'speaking' ? 'idle' : v)
    window.speechSynthesis.speak(utterance)
  }, [setError])
  const listen = useCallback(() => {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition || window.msSpeechRecognition
    if (!Rec || !('speechSynthesis' in window)) { setVoice('unsupported'); return }
    const recognition = new Rec()
    recognition.lang = 'fr-FR'
    recognition.continuous = true
    recognition.interimResults = false
    recognition.onresult = event => {
      const heard = Array.from(event.results).map(r => r[0].transcript).join(' ').toLocaleLowerCase('fr-FR')
      if (heard.includes('waky waky')) { recognition.stop(); setVoice('idle'); say() }
    }
    recognition.onerror = event => { setVoice(['not-allowed', 'service-not-allowed', 'denied'].includes(event.error) ? 'denied' : 'idle') }
    recognition.onend = () => setVoice(v => (v === 'listening' || v === 'speaking') ? 'idle' : v)
    rec.current = recognition
    setVoice('listening')
    recognition.start()
  }, [say])
  const toggle = useCallback(() => {
    if (voice === 'speaking') { window.speechSynthesis.cancel(); setVoice('idle'); return }
    if (voice === 'listening') { rec.current?.stop(); setVoice('idle'); return }
    if (!supported) { setVoice('unsupported'); return }
    listen()
  }, [voice, supported, listen])
  useEffect(() => () => { rec.current?.stop(); window.speechSynthesis?.cancel() }, [])
  return <div className="waky">
    <button className={`waky-fab voice-${voice}`} onClick={toggle} disabled={voice === 'unsupported'} aria-label="Waky Waky — point du jour" title="Waky waky, Hunter ! Dis « waky waky » ou clique pour le point du jour">
      <span className="waky-mic"><Mic /></span>
      <span className="waky-label"><strong>Waky Waky</strong><small className={`waky-state voice-${voice}`}>{voiceStates[voice]}</small></span>
    </button>
    {spoken && <aside className="waky-card" aria-live="polite"><strong><Volume2 /> Point du jour</strong><p>{spoken}</p><Button icon={X} className="icon" aria-label="Fermer le point" onClick={() => setSpoken('')} /></aside>}
  </div>
}
function Modal({ title, close, children, wide }) {
  const ref = useRef(null)
  useEffect(() => { const previous = document.activeElement; const dialog = ref.current; dialog.showModal(); return () => { dialog.close(); previous?.focus() } }, [])
  return <dialog ref={ref} className={wide ? 'drawer' : ''} onCancel={close} onClick={e => { if (e.target === ref.current) close() }}><div className="dialog-inner"><header className="section-head"><h2>{title}</h2><Button icon={X} className="icon" aria-label="Fermer" title="Fermer" onClick={close} /></header>{children}</div></dialog>
}

function Application({ row, close, act, busy, gmail }) {
  const [draft, setDraft] = useState({ status: row.status, notes: row.notes || '', recipient: row.recipient || '', email_subject: row.email_subject || '', email_body: row.email_body || '' })
  const [dirty, setDirty] = useState(false)
  const patch = (key, value) => { setDirty(true); setDraft(d => ({ ...d, [key]: value })) }
  const base = `/applications/${encodeURIComponent(row.id)}`
  const ready = ['approved', 'draft_created'].includes(row.status)
  return <Modal title="Dossier de candidature" close={close} wide><div className="detail-title"><span className="kicker">{row.company || 'Entreprise à identifier'}</span><h2>{row.title || 'Offre sans titre'}</h2><p><MapPin size={14} />{row.location || 'Lieu à vérifier'} · {row.source || 'Import manuel'}</p>{url(row.url) && <a href={url(row.url)} target="_blank" rel="noreferrer">Consulter l’offre <ExternalLink size={14} /></a>}</div>
    <div className="detail-metrics"><div><small>Adéquation</small><strong>{row.qualification_score == null ? 'À analyser' : `${row.qualification_score}/100`}</strong></div><div><small>Date de l’offre</small><strong>{date(row.offer_date)}</strong></div><div><small>Dernière mise à jour</small><strong>{date(row.updated_at)}</strong></div></div>
    <section className="detail-section"><h3>Analyse de l’offre</h3>{(row.qualification_reasons || []).map((r, i) => <p className="reason" key={i}><Check size={15} />{display(r)}</p>)}{(row.missing_information || []).map((r, i) => <p className="warning" key={i}>{display(r)}</p>)}{!row.qualification_reasons?.length && <p className="muted">Analyse en attente.</p>}{row.content && <details><summary>Description de l’offre</summary><p className="prewrap">{row.content}</p></details>}{row.assessment && <details><summary>Évaluation détaillée</summary><pre>{display(row.assessment)}</pre></details>}{row.review && <details><summary>Contrôle du dossier</summary><pre>{display(row.review)}</pre></details>}</section>
    <form onSubmit={async e => { e.preventDefault(); const changes = Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== (row[key] || ''))); if (await act(`save-${row.id}`, () => api(base, 'PATCH', changes), 'Dossier enregistré')) setDirty(false) }}>
      <section className="detail-section"><h3>Suivi</h3><Field label="Statut"><select value={draft.status} onChange={e => patch('status', e.target.value)}>{Object.entries(statuses).map(([k, v]) => <option key={k} value={k} disabled={['approved', 'draft_created', 'preparing'].includes(k) && k !== row.status}>{v}</option>)}</select></Field><Field label="Notes personnelles"><textarea rows="3" value={draft.notes} onChange={e => patch('notes', e.target.value)} /></Field></section>
      <section className="detail-section"><h3>Email de candidature</h3><Field label="Destinataire"><input type="email" value={draft.recipient} onChange={e => patch('recipient', e.target.value)} /></Field><Field label="Objet"><input value={draft.email_subject} onChange={e => patch('email_subject', e.target.value)} /></Field><Field label="Message"><textarea rows="11" value={draft.email_body} onChange={e => patch('email_body', e.target.value)} /></Field><Button icon={Save} busy={busy === `save-${row.id}`} disabled={!dirty || !!busy} type="submit">Enregistrer les modifications</Button></section>
    </form>
    <section className="detail-section"><h3>Documents</h3>{row.artifacts?.length ? row.artifacts.map((a, i) => url(a.url) && <a className="attachment" href={url(a.url)} key={i} target="_blank" rel="noreferrer"><FileText />{a.name}<Download /></a>) : <p className="muted">Aucun document généré.</p>}</section>
    <div className="drawer-actions"><Button icon={FileText} disabled={!!busy || dirty || ['preparing', 'applied', 'interview', 'offer', 'rejected', 'draft_created'].includes(row.status)} onClick={() => act('prepare', () => api(`${base}/prepare`, 'POST', {}), 'Préparation lancée')}>Préparer</Button><Button icon={ShieldCheck} disabled={!!busy || dirty || !row.email_body || ready} onClick={() => act('approve', () => api(`${base}/approve`, 'POST', {}), 'Dossier validé')}>Valider le dossier</Button><Button className="primary" icon={Mail} disabled={!!busy || dirty || !ready || !gmail} onClick={() => act('gmail-draft', () => api(`${base}/draft`, 'POST', {}), 'Brouillon Gmail créé')}>Créer le brouillon Gmail</Button>{!!row.artifacts?.length && <a className="button" href={`/api${base}/export.eml`}><Download />Exporter .eml</a>}</div>{dirty && <p className="warning">Modifications non enregistrées.</p>}<p className="muted small">{gmail ? 'Envoi final depuis Gmail, après votre validation.' : 'Gmail non connecté. Connexion disponible dans Profil.'}</p>
  </Modal>
}

function Profile({ profile, settings, connections, act, busy }) {
  const [json, setJson] = useState(JSON.stringify(profile, null, 2))
  const [criteria, setCriteria] = useState({ role: profile.target_criteria?.role || 'PFE Data science / Actuariat', locations: (profile.target_criteria?.countries || ['France', 'Bruxelles', 'Suisse', 'Tunisie']).join(', '), start_date: profile.target_criteria?.start_date || 'Fin janvier 2027' })
  const [budgets, setBudgets] = useState({ daily_search_limit: 16, monthly_search_limit: 800, daily_llm_limit: 40, auto_watch: false, watch_hour: 9, ...settings })
  const [parseError, setParseError] = useState('')
  return <div className="settings-layout"><section><h2>Objectif professionnel</h2><form onSubmit={e => { e.preventDefault(); try { const parsed = JSON.parse(json); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Le profil doit être un objet JSON.'); setParseError(''); act('profile', () => api('/profile', 'PUT', { ...parsed, target_criteria: { ...parsed.target_criteria, role: criteria.role, start_date: criteria.start_date, countries: criteria.locations.split(',').map(s => s.trim()).filter(Boolean) } }), 'Profil enregistré') } catch (err) { setParseError(err.message) } }}><Field label="Poste recherché"><input required value={criteria.role} onChange={e => setCriteria({ ...criteria, role: e.target.value })} /></Field><Field label="Destinations"><input required value={criteria.locations} onChange={e => setCriteria({ ...criteria, locations: e.target.value })} /></Field><Field label="Disponibilité"><input required type="text" value={criteria.start_date} onChange={e => setCriteria({ ...criteria, start_date: e.target.value })} /></Field><details className="advanced"><summary>Profil complet · JSON</summary><textarea aria-label="Profil JSON" className="code" rows="20" value={json} onChange={e => setJson(e.target.value)} spellCheck="false" /></details>{parseError && <p role="alert" className="warning">{parseError}</p>}<Button className="primary" icon={Save} type="submit" busy={busy === 'profile'} disabled={!!busy}>Enregistrer le profil</Button></form></section>
    <section><h2>Budget & veille</h2><form onSubmit={e => { e.preventDefault(); act('settings', () => api('/settings', 'PATCH', budgets), 'Préférences enregistrées') }}>{[['daily_search_limit', 'Recherches par jour'], ['monthly_search_limit', 'Recherches par mois'], ['daily_llm_limit', 'Appels IA par jour']].map(([key, label]) => <Field label={label} key={key}><input type="number" min="1" max={key === 'monthly_search_limit' ? 800 : key === 'daily_llm_limit' ? 80 : 64} required value={budgets[key]} onChange={e => setBudgets({ ...budgets, [key]: e.target.value === '' ? '' : Number(e.target.value) })} /></Field>)}<label className="toggle"><input type="checkbox" checked={budgets.auto_watch} onChange={e => setBudgets({ ...budgets, auto_watch: e.target.checked })} />Veille automatique</label><Field label="Heure de veille (serveur)"><input type="number" min="0" max="23" required value={budgets.watch_hour} onChange={e => setBudgets({ ...budgets, watch_hour: Number(e.target.value) })} /></Field><Button icon={Save} type="submit" disabled={!!busy}>Enregistrer les limites</Button></form><h2 className="spaced">Connexions</h2>{['groq', 'tavily', 'gmail'].map(k => <div className="connection-row" key={k}><strong>{k === 'gmail' ? 'Gmail' : k === 'groq' ? 'Groq' : 'Tavily'}</strong><span className={connections[k] ? 'connected' : 'muted'}>{connections[k] ? 'Connecté' : 'Non connecté'}</span></div>)}<Button icon={Mail} disabled={!!busy} onClick={() => act('connect', async () => { const result = await api('/gmail/connect', 'POST', {}); const link = url(result.auth_url || result.url); if (link) window.location.assign(link); return result }, 'Connexion Gmail initiée')}>Connecter Gmail</Button></section></div>
}

export default function App() {
  const [authed, setAuthed] = useState(() => { try { return localStorage.getItem(SESSION_KEY) === '1' } catch { return false } })
  const [data, setData] = useState(initial)
  const [page, setPage] = useState('opportunities')
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const lock = useRef(false)
  const [selected, setSelected] = useState(null)
  const [importOpen, setImportOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [source, setSource] = useState('all')
  const [sort, setSort] = useState('score')
  const [runId, setRunId] = useState('all')
  const [max, setMax] = useState(8)
  const fetchId = useRef(0)
  const refresh = useCallback(async signal => { const id = ++fetchId.current; const value = await api('/overview', 'GET', undefined, signal); if (id === fetchId.current) { setData({ ...initial, ...value }); setLoaded(true) } }, [])
  const running = data.runs.some(active)
  useEffect(() => { const controller = new AbortController(); let timer; let disposed = false; const poll = async () => { try { await refresh(controller.signal); setError('') } catch (e) { if (e.name !== 'AbortError') setError(e.message) } finally { if (!disposed) timer = setTimeout(poll, running ? 3000 : 10000) } }; poll(); return () => { disposed = true; controller.abort(); clearTimeout(timer) } }, [running, refresh])
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(''), 6000); return () => clearTimeout(t) }, [notice])
  const act = async (key, action, success) => { if (lock.current) return false; lock.current = true; setBusy(key); setError(''); try { await action(); setNotice(success || 'Enregistré'); await refresh(); return true } catch (e) { setError(e.message); return false } finally { lock.current = false; setBusy('') } }
  const filtered = useMemo(() => data.applications.filter(r => (status === 'all' || r.status === status) && (source === 'all' || r.source === source) && `${r.title} ${r.company} ${r.location}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).sort((a, b) => sort === 'score' ? (b.qualification_score ?? -1) - (a.qualification_score ?? -1) : sort === 'company' ? (a.company || '').localeCompare(b.company || '') : (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)), [data.applications, query, status, source, sort])
  const events = data.events.filter(e => runId === 'all' || String(e.run_id) === runId).slice().sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0))
  const row = data.applications.find(r => r.id === selected)
  const nav = [['opportunities', 'Opportunités', Inbox], ['team', 'Équipe', Users], ['companies', 'Entreprises', Building2], ['profile', 'Profil & limites', Settings2]]
  if (!authed) return <LoginScreen onLogin={() => { try { localStorage.setItem(SESSION_KEY, '1') } catch {} setAuthed(true) }} />
  const logout = () => { try { localStorage.removeItem(SESSION_KEY) } catch {} setAuthed(false) }
  return <div className="shell"><aside className="sidebar"><a className="brand" href="/" aria-label="Hunter × Hunter accueil"><span className="brand-mark"><GitBranch /></span>Hunter<span className="brand-dot">×</span></a><div className="workspace-name">HUNTER × HUNTER</div><nav aria-label="Navigation principale">{nav.map(([key, label, Icon]) => <button key={key} className={page === key ? 'nav-active' : ''} aria-current={page === key ? 'page' : undefined} onClick={() => setPage(key)}><Icon /><span>{label}</span>{key === 'opportunities' && <small>{data.applications.length}</small>}</button>)}</nav><div className="sidebar-bottom"><span className="kicker">CAMPAGNE PFE 2027</span><strong>Data science & actuariat</strong><span>À partir de fin janvier</span><div className="sidebar-status"><i className={running ? 'live' : ''} />{running ? 'Équipe en activité' : 'Équipe disponible'}</div></div><button className="logout" onClick={logout} disabled={!!busy}><LogOut />Se déconnecter</button></aside>
    <main><header className="topbar"><div className="breadcrumb">Mon espace <ChevronRight /> <strong>{nav.find(n => n[0] === page)?.[1]}</strong></div><div className="actions"><span className="tier">Budget gratuit</span><Button className="icon" icon={RefreshCw} title="Actualiser" aria-label="Actualiser" disabled={!!busy} onClick={() => act('refresh', async () => {}, 'Données actualisées')} /><Button className="primary" icon={running ? LoaderCircle : Play} disabled={running || !!busy || !loaded} onClick={() => act('run', () => api('/run', 'POST', { max }), 'Recherche lancée')}>{running ? 'En cours' : 'Lancer la recherche'}</Button></div></header>
      <div className="page"><div className="page-heading"><div><p className="kicker">PFE · JANVIER 2027</p><h1>{page === 'opportunities' ? 'Vos prochaines opportunités' : page === 'team' ? 'Le travail de votre équipe' : page === 'companies' ? 'Vos entreprises cibles' : 'Votre profil de recherche'}</h1></div><span className="target"><MapPin />{(data.profile.target_criteria?.countries || ['France', 'Bruxelles', 'Suisse', 'Tunisie']).join(' · ')}</span></div>
      {error && <div className="error" role="alert">{error}<Button icon={X} className="icon" aria-label="Masquer l’erreur" onClick={() => setError('')} /></div>}{notice && <div className="notice" role="status"><Check />{notice}</div>}
      {!loaded ? <Empty title={error ? 'Connexion au serveur indisponible' : 'Chargement de votre espace…'} /> : <>
      {page === 'opportunities' && <><div className="stats">{[['Offres collectées', data.applications.length, Inbox], ['À vérifier', data.applications.filter(r => ['review_needed', 'prepared'].includes(r.status)).length, ShieldCheck], ['Brouillons prêts', data.applications.filter(r => r.status === 'draft_created').length, Mail], ['Entretiens', data.applications.filter(r => r.status === 'interview').length, Users]].map(([label, value, Icon]) => <div key={label}><span><Icon />{label}</span><strong>{value.toString().padStart(2, '0')}</strong></div>)}</div><div className="section-head"><h2>Pipeline de candidatures <small>{filtered.length}</small></h2><Button icon={Plus} onClick={() => setImportOpen(true)}>Ajouter une offre</Button></div><div className="filters"><label className="search"><Search /><input aria-label="Rechercher une offre" placeholder="Poste, entreprise, lieu…" value={query} onChange={e => setQuery(e.target.value)} /></label><select aria-label="Filtrer par statut" value={status} onChange={e => setStatus(e.target.value)}><option value="all">Tous les statuts</option>{Object.entries(statuses).map(([k, v]) => <option value={k} key={k}>{v}</option>)}</select><select aria-label="Filtrer par source" value={source} onChange={e => setSource(e.target.value)}><option value="all">Toutes les sources</option>{[...new Set(data.applications.map(r => r.source).filter(Boolean))].sort().map(s => <option key={s}>{s}</option>)}</select><select aria-label="Trier les offres" value={sort} onChange={e => setSort(e.target.value)}><option value="score">Meilleure adéquation</option><option value="recent">Plus récentes</option><option value="company">Entreprise A–Z</option></select></div><div className="offer-table"><div className="table-head"><span>Poste / entreprise</span><span>Destination</span><span>Adéquation</span><span>Statut</span><span /></div>{filtered.map(r => <button className="offer-row" key={r.id} onClick={() => setSelected(r.id)}><span className="offer-main"><span className="company-mark">{(r.company || '?').slice(0, 2).toUpperCase()}</span><span><strong>{r.title || 'Offre sans titre'}</strong><small>{r.company || 'Entreprise à identifier'} · {r.source || 'Import manuel'}</small></span></span><span className="location">{r.location || 'À vérifier'}</span><span className="score">{r.qualification_score == null ? '—' : <><b>{r.qualification_score}</b><small>/100</small><meter min="0" max="100" value={r.qualification_score} aria-label="Score d’adéquation" /></>}</span><span className={`badge status-${r.status}`}>{statuses[r.status] || r.status}</span><ChevronRight /></button>)}{!filtered.length && <Empty title={data.applications.length ? 'Aucune offre ne correspond aux filtres' : 'Votre prochaine étape commence ici'}>{data.applications.length ? 'Aucun résultat.' : 'Aucune offre collectée pour le moment.'}</Empty>}</div></>}
      {page === 'team' && <><div className="team-toolbar"><h2><span className={`status-dot ${running ? 'live' : ''}`} />{running ? 'Mission en cours' : 'En attente de mission'}</h2><div className="actions"><Field label="Offres prioritaires"><input type="number" min="1" max="30" value={max} onChange={e => setMax(Math.max(1, Math.min(30, Number(e.target.value) || 1)))} /></Field>{'speechSynthesis' in window && <Button icon={Volume2} disabled={!data.runs.some(r => r.summary)} onClick={() => { window.speechSynthesis.cancel(); const speech = new SpeechSynthesisUtterance(display(data.runs.find(r => r.summary)?.summary)); speech.lang = 'fr-FR'; window.speechSynthesis.speak(speech) }}>Écouter le bilan</Button>}</div></div><div className="agent-flow">{agents.map(([key, name, Icon]) => { const event = data.events.filter(e => e.agent === key).sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0))[0]; const working = event && data.runs.some(r => r.id === event.run_id && active(r) && r.stage === key); return <div className={`agent-node ${working ? 'working' : ''}`} key={key}><Icon /><strong>{name}</strong><small>{working ? 'En activité' : event ? 'Intervenu' : 'En attente'}</small></div> })}</div><div className="team-columns"><section><div className="section-head"><h2>Journal de mission</h2><select aria-label="Filtrer par mission" value={runId} onChange={e => setRunId(e.target.value)}><option value="all">Toutes les missions</option>{data.runs.map(r => <option key={r.id} value={r.id}>{date(r.created_at)} · {r.kind || 'Recherche'}</option>)}</select></div><div className="event-list">{events.map(e => <article className="event" key={e.id}><span className={`event-dot ${e.event_type === 'error' ? 'failed' : ''}`} /><div><div className="event-meta"><strong>{agents.find(a => a[0] === e.agent)?.[1] || e.agent}</strong>{e.recipient && <><ArrowRight /><span>{agents.find(a => a[0] === e.recipient)?.[1] || e.recipient}</span></>}<time>{date(e.created_at)}</time></div><p>{e.message}</p><small>{e.event_type}</small></div></article>)}{!events.length && <Empty title="Aucun événement enregistré" />}</div></section><aside className="run-history"><h2>Missions</h2>{data.runs.map(r => <article className="run" key={r.id}><div className="section-head"><strong>{r.kind || 'Recherche'}</strong><span className="badge">{r.status}</span></div><small>{date(r.created_at)} · {r.stage || 'En attente'}</small>{r.summary && <p>{display(r.summary)}</p>}{(r.errors || []).map((e, i) => <p className="warning" key={i}>{display(e)}</p>)}{active(r) ? <Button icon={Square} disabled={!!busy} onClick={() => act('cancel', () => api(`/runs/${encodeURIComponent(r.id)}/cancel`, 'POST', {}), 'Arrêt demandé')}>Arrêter</Button> : ['cancelled', 'failed', 'budget_paused', 'interrupted', 'partial'].includes(r.status) && <Button icon={Play} disabled={!!busy || running} onClick={() => act('resume', () => api(`/runs/${encodeURIComponent(r.id)}/resume`, 'POST', {}), 'Mission reprise')}>Reprendre</Button>}</article>)}{!data.runs.length && <p className="muted">Aucune mission.</p>}<h3>Consommation</h3>{[['search_today', 'Recherches aujourd’hui', data.settings.daily_search_limit], ['search_month', 'Recherches ce mois', data.settings.monthly_search_limit], ['llm_today', 'Appels IA aujourd’hui', data.settings.daily_llm_limit]].map(([key, label, limit]) => <div className="usage" key={key}><span>{label}<b>{data.usage[key] ?? 0} / {limit ?? '—'}</b></span><progress value={data.usage[key] || 0} max={limit || 1} /></div>)}</aside></div></>}
      {page === 'profile' && <Profile profile={data.profile} settings={data.settings} connections={data.connections} act={act} busy={busy} />}
      {page === 'companies' && <><form className="company-form" onSubmit={async e => { e.preventDefault(); const form = e.currentTarget; if (await act('company', () => api('/companies', 'POST', Object.fromEntries(new FormData(form))), 'Entreprise ajoutée')) form.reset() }}><Field label="Entreprise"><input name="name" required /></Field><Field label="Site carrière"><input name="website" type="url" placeholder="https://" /></Field><Field label="Priorité"><select name="priority"><option value="normal">Normale</option><option value="high">Haute</option></select></Field><Field label="Notes"><input name="notes" /></Field><Button icon={Plus} className="primary" type="submit" disabled={!!busy}>Ajouter</Button></form><div className="company-list">{data.companies.map(c => <article key={c.id}><span className="company-mark"><Building2 /></span><div><h3>{c.name}</h3>{url(c.website) && c.website && <a href={url(c.website)} target="_blank" rel="noreferrer">Site carrière <ExternalLink size={13} /></a>}{c.notes && <p>{c.notes}</p>}</div><span className="badge">{c.priority === 'high' ? 'Priorité haute' : 'Normale'}</span><Button icon={Trash2} className="icon danger" title={`Retirer ${c.name}`} aria-label={`Retirer ${c.name}`} disabled={!!busy} onClick={() => act('delete-company', () => api(`/companies/${encodeURIComponent(c.id)}`, 'DELETE'), 'Entreprise retirée')} /></article>)}{!data.companies.length && <Empty title="Aucune entreprise cible" />}</div></>}
      </>}<footer><span>Hunter × Hunter · PFE 2027</span><span><span className="status-dot" />{loaded ? 'Données du registre' : 'Connexion en attente'}</span></footer></div>
    </main>{row && <Application key={`${row.id}-${row.updated_at}`} row={row} close={() => setSelected(null)} act={act} busy={busy} gmail={data.connections.gmail} />}{importOpen && <Modal title="Ajouter une offre" close={() => setImportOpen(false)}><form onSubmit={async e => { e.preventDefault(); const values = Object.fromEntries(new FormData(e.currentTarget)); if (await act('import', () => api('/applications', 'POST', values), 'Offre ajoutée')) setImportOpen(false) }}><Field label="Lien de l’offre"><input name="url" type="url" required autoFocus /></Field><Field label="Intitulé"><input name="title" required /></Field><Field label="Entreprise"><input name="company" /></Field><Field label="Localisation"><input name="location" /></Field><Field label="Description"><textarea name="content" rows="6" /></Field><Button icon={Plus} className="primary" type="submit" busy={busy === 'import'} disabled={!!busy}>Ajouter au pipeline</Button></form></Modal>}<WakyVoice setError={setError} /></div>
}
