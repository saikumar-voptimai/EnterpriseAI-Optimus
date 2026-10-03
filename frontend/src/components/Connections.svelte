<script>
  import { onMount } from 'svelte';
  import { CalendarDays, Cable, Database, Video, Mail, MessageSquare, RefreshCw, Trash2, ExternalLink, Check, Plus, FileText, Search, Download } from 'lucide-svelte';
  import { api, query } from '../api.js';
  import DeliveryPanel from './DeliveryPanel.svelte';

  let { workspaceId = null, canManage = true, isAdmin = false, workspaces = [] } = $props();

  // Integration kinds as people know them; transport details stay on the server.
  const KINDS = {
    google: { label: 'Google Workspace', icon: CalendarDays },
    microsoft: { label: 'Microsoft 365', icon: CalendarDays },
    zoom: { label: 'Zoom', icon: Video },
    slack_webhook: { label: 'Slack', icon: MessageSquare },
    teams_workflow: { label: 'Microsoft Teams', icon: Mail },
    influxdb: { label: 'Process data', icon: Database },
  };
  const EDITORS = {
    influxdb: { title: 'Add a process data source', name: 'Plant process data' },
    zoom: { title: 'Configure Zoom', name: 'Zoom meetings' },
    slack_webhook: { title: 'Add a Slack channel', name: 'Slack notifications' },
    teams_workflow: { title: 'Add a Teams channel', name: 'Teams notifications' },
  };

  let rows = $state([]), capabilities = $state(null), events = $state([]), resources = $state({});
  let editing = $state(null), name = $state(''), url = $state(''), token = $state(''), allowPrivate = $state(false);
  let accountId = $state(''), clientId = $state(''), clientSecret = $state(''), includeTranscripts = $state(false);
  let busy = $state(false), error = $state(''), notice = $state('');
  let driveFor = $state(null), driveQuery = $state(''), driveFiles = $state([]), driveTarget = $state('');

  const kind = connection => KINDS[connection.provider] || { label: 'Connection', icon: Cable };
  const fmt = value => value ? new Date(value).toLocaleString() : 'Not yet synchronized';
  const canSync = connection => ['google', 'microsoft'].includes(connection.provider);
  const writableWorkspaces = $derived(workspaces.filter(w => ['member', 'manager'].includes(w.membership_role)));

  onMount(load);

  async function load() {
    try {
      const [all, caps] = await Promise.all([api('/connections' + query({ workspace_id: workspaceId })), api('/connections/capabilities')]);
      rows = all.filter(c => workspaceId ? c.workspace_id === workspaceId : !c.workspace_id);
      capabilities = caps;
      if (!workspaceId) {
        const end = new Date(Date.now() + 7 * 86400000);
        events = await api('/calendar/events' + query({ start: new Date().toISOString(), end: end.toISOString() }));
      }
    } catch (e) { error = e.message; }
  }

  async function run(task, success) {
    busy = true; error = ''; notice = '';
    try { await task(); if (success) notice = success; } catch (e) { error = e.message; } finally { busy = false; }
  }

  const signIn = provider => run(async () => {
    const body = provider === 'microsoft' ? { include_transcripts: includeTranscripts } : undefined;
    const result = await api(`/connections/${provider}/start`, { method: 'POST', body: body || {} });
    window.location.assign(result.authorization_url);
  });

  function add(provider) {
    editing = provider; name = EDITORS[provider].name;
    url = ''; token = ''; accountId = ''; clientId = ''; clientSecret = ''; allowPrivate = false;
  }

  function save(event) {
    event.preventDefault();
    run(async () => {
      const provider = editing;
      const config = provider === 'influxdb' ? { url, bucket_ids: [], allow_private_network: allowPrivate } : {};
      const credentials = provider === 'zoom' ? { account_id: accountId, client_id: clientId, client_secret: clientSecret }
        : provider === 'influxdb' ? { token } : { webhook_url: url };
      await api('/connections', { method: 'POST', body: { provider, name, workspace_id: workspaceId, config, credentials } });
      editing = null;
      await load();
    }, 'Connection saved. Open its settings to choose what it may use.');
  }

  const inspect = connection => run(async () => {
    resources = { ...resources, [connection.id]: await api(`/connections/${connection.id}/resources`) };
    await load();
  });

  const sync = connection => run(async () => {
    await api(`/connections/${connection.id}/sync`, { method: 'POST', body: {} });
    await load();
  }, 'Calendar synchronized.');

  function disconnect(connection) {
    if (!confirm(`Disconnect ${connection.name}?`)) return;
    run(async () => { await api(`/connections/${connection.id}`, { method: 'DELETE' }); await load(); }, 'Connection disconnected.');
  }

  const saveConfig = (connection, config, success = 'Selection saved.') => run(async () => {
    await api(`/connections/${connection.id}`, { method: 'PATCH', body: { config } });
    await load();
  }, success);

  function toggle(list, id, enabled) {
    return enabled ? [...new Set([...(list || []), id])] : (list || []).filter(v => v !== id);
  }

  async function openDrive(connection) {
    driveFor = driveFor === connection.id ? null : connection.id;
    driveFiles = []; driveQuery = '';
    if (driveFor) await searchDrive();
  }

  const searchDrive = () => run(async () => {
    driveFiles = await api(`/connections/${driveFor}/drive/files` + query({ q: driveQuery }));
  });

  const importFile = file => run(async () => {
    const body = { file_id: file.id, ...(driveTarget ? { workspace_id: driveTarget } : {}) };
    await api(`/connections/${driveFor}/drive/import`, { method: 'POST', body });
  }, `${file.name} imported into ${driveTarget ? writableWorkspaces.find(w => w.id === driveTarget)?.name : 'your documents'}.`);
</script>

{#if error}<div class="banner error" role="alert">{error}</div>{/if}
{#if notice}<div class="banner success" role="status"><Check size={16}/>{notice}</div>{/if}

<div class="info-strip">
  <Cable size={20}/>
  <p>{workspaceId
    ? 'Connections here are shared with this workspace and used only within its permissions. Plant data is read-only.'
    : 'Connect your calendar, files, meetings and messaging. Sign-in details stay protected on the server.'}</p>
</div>

{#if canManage}
  <div class="connection-catalog">
    {#if !workspaceId}
      <article class="card">
        <span class="feature-icon"><CalendarDays size={25}/></span>
        <h2>Google Workspace</h2>
        <p>Calendar, Drive files and Google Meet transcripts.</p>
        <button class="button primary" onclick={() => signIn('google')} disabled={busy || !capabilities?.google?.configured}><ExternalLink size={15}/>Connect Google</button>
        {#if capabilities && !capabilities.google?.configured}<small class="help">An administrator needs to enable Google sign-in first.</small>{/if}
      </article>
      <article class="card">
        <span class="feature-icon"><CalendarDays size={25}/></span>
        <h2>Microsoft 365</h2>
        <p>Outlook calendar and Teams meeting transcripts.</p>
        <label class="check-row"><input type="checkbox" bind:checked={includeTranscripts}/><span>Include meeting transcripts</span></label>
        <button class="button primary" onclick={() => signIn('microsoft')} disabled={busy || !capabilities?.microsoft?.configured}><ExternalLink size={15}/>Connect Microsoft</button>
        {#if capabilities && !capabilities.microsoft?.configured}<small class="help">An administrator needs to enable Microsoft sign-in first.</small>{/if}
      </article>
    {:else}
      <article class="card">
        <span class="feature-icon"><Database size={25}/></span>
        <h2>Process data</h2>
        <p>Read approved plant measurements in this workspace.</p>
        <button class="button" onclick={() => add('influxdb')} disabled={!isAdmin}><Plus size={15}/>Add data source</button>
        {#if !isAdmin}<small class="help">An administrator adds plant data sources.</small>{/if}
      </article>
    {/if}
    <article class="card">
      <span class="feature-icon"><Video size={25}/></span>
      <h2>Zoom</h2>
      <p>Bring recorded meeting transcripts into Meeting Rooms.</p>
      <button class="button" onclick={() => add('zoom')} disabled={!isAdmin}><Plus size={15}/>Configure Zoom</button>
    </article>
    <article class="card">
      <span class="feature-icon"><MessageSquare size={25}/></span>
      <h2>Slack</h2>
      <p>Send approved updates and alerts to a Slack channel.</p>
      <button class="button" onclick={() => add('slack_webhook')}><Plus size={15}/>Add Slack channel</button>
    </article>
    <article class="card">
      <span class="feature-icon"><Mail size={25}/></span>
      <h2>Microsoft Teams</h2>
      <p>Send approved updates and alerts to a Teams channel.</p>
      <button class="button" onclick={() => add('teams_workflow')}><Plus size={15}/>Add Teams channel</button>
    </article>
  </div>
{/if}

{#if editing}
  <section class="card connection-editor">
    <h2>{EDITORS[editing].title}</h2>
    <form onsubmit={save}>
      <label>Connection name<input bind:value={name} required/></label>
      {#if editing === 'influxdb'}
        <label>Server address<input type="url" bind:value={url} required/></label>
        <label class="check-row"><input type="checkbox" bind:checked={allowPrivate}/><span>The server is on the plant network</span></label>
        <label>Read-only access token<input type="password" bind:value={token} required autocomplete="new-password"/></label>
        <p class="help">After saving, open the connection's settings to choose which data sets this workspace may use.</p>
      {:else if editing === 'zoom'}
        <label>Account ID<input bind:value={accountId} required/></label>
        <label>Client ID<input bind:value={clientId} required/></label>
        <label>Client secret<input type="password" bind:value={clientSecret} required autocomplete="new-password"/></label>
      {:else}
        <label>{editing === 'slack_webhook' ? 'Slack webhook address' : 'Teams workflow address'}<input type="password" bind:value={url} required autocomplete="new-password"/></label>
      {/if}
      <div class="form-actions">
        <button type="button" class="button" onclick={() => editing = null} disabled={busy}>Cancel</button>
        <button class="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save connection'}</button>
      </div>
    </form>
  </section>
{/if}

<div class="section-heading"><h2>Connected sources</h2><button class="text-button" onclick={load}><RefreshCw size={15}/>Refresh</button></div>
<div class="stack">
  {#each rows as connection (connection.id)}
    {@const Icon = kind(connection).icon}
    <article class="card">
      <div class="row-card">
        <span class="feature-icon"><Icon size={23}/></span>
        <div class="grow">
          <h3>{connection.name}</h3>
          <p class="help">{[kind(connection).label, connection.config?.account, canSync(connection) ? `Last sync: ${fmt(connection.last_synced_at)}` : ''].filter(Boolean).join(' · ')}</p>
        </div>
        <span class="tag" class:green={connection.status === 'connected'}>{connection.status === 'connected' ? 'Connected' : connection.status === 'pending' ? 'Pending' : connection.status === 'error' ? 'Needs attention' : 'Disconnected'}</span>
        <div class="row-actions">
          {#if canManage && connection.status !== 'disconnected' && connection.provider !== 'zoom'}
            <button class="button" onclick={() => inspect(connection)} disabled={busy}>Settings</button>
          {/if}
          {#if canSync(connection) && connection.status !== 'disconnected'}<button class="button" onclick={() => sync(connection)} disabled={busy}><RefreshCw size={14}/>Sync</button>{/if}
          {#if connection.provider === 'google' && connection.status === 'connected'}<button class="button" onclick={() => openDrive(connection)} disabled={busy}><FileText size={14}/>Drive files</button>{/if}
          {#if canManage}<button class="icon-button" aria-label={`Disconnect ${connection.name}`} onclick={() => disconnect(connection)} disabled={busy}><Trash2 size={16}/></button>{/if}
        </div>
      </div>
      {#if connection.last_error}<p class="form-error">{connection.last_error}</p>{/if}

      {#if resources[connection.id]?.calendars}
        <div class="connection-resources">
          <h3>Calendars to include</h3>
          {#each resources[connection.id].calendars as calendar}
            <label class="check-row"><input type="checkbox" checked={(connection.config?.calendar_ids || []).includes(calendar.id)} onchange={e => saveConfig(connection, { calendar_ids: toggle(connection.config?.calendar_ids, calendar.id, e.currentTarget.checked) })}/><span>{calendar.name}</span></label>
          {/each}
        </div>
      {:else if resources[connection.id]?.buckets}
        {@const organizations = resources[connection.id].organizations || []}
        <div class="connection-resources">
          {#if organizations.length > 1}
            <label>Organization<select value={connection.config?.org_id || ''} onchange={e => saveConfig(connection, { org_id: e.currentTarget.value, bucket_ids: [] })} disabled={!isAdmin}><option value="">Choose an organization</option>{#each organizations as org, i}<option value={org.id}>{org.name.startsWith('Organization ') ? `Organization ${i + 1}` : org.name}</option>{/each}</select></label>
          {/if}
          <fieldset>
            <legend>Data sets this workspace may use</legend>
            {#each resources[connection.id].buckets.filter(b => !connection.config?.org_id || b.org_id === connection.config.org_id) as bucket}
              <label class="check-row"><input type="checkbox" checked={(connection.config?.bucket_ids || []).includes(bucket.id)} onchange={e => saveConfig(connection, { bucket_ids: toggle(connection.config?.bucket_ids, bucket.id, e.currentTarget.checked) })} disabled={!isAdmin}/><span>{bucket.name}</span></label>
            {/each}
          </fieldset>
        </div>
      {:else if resources[connection.id]}
        <p class="help connection-resources">{resources[connection.id].detail || 'Connection verified.'}</p>
      {/if}

      {#if driveFor === connection.id}
        <div class="connection-resources">
          <h3>Import from Google Drive</h3>
          <form class="form-grid" onsubmit={e => { e.preventDefault(); searchDrive(); }}>
            <label>Search files<input bind:value={driveQuery} placeholder="File name"/></label>
            <label>Import into<select bind:value={driveTarget}><option value="">My documents</option>{#each writableWorkspaces as w}<option value={w.id}>{w.name}</option>{/each}</select></label>
            <div class="form-actions"><button class="button" disabled={busy}><Search size={14}/>Search</button></div>
          </form>
          {#each driveFiles as file}
            <div class="row-card"><FileText size={16}/><span class="grow">{file.name}<small class="help"> · {new Date(file.modified_at).toLocaleDateString()}</small></span><button class="button compact" onclick={() => importFile(file)} disabled={busy}><Download size={14}/>Import</button></div>
          {:else}
            <p class="help">No matching files.</p>
          {/each}
        </div>
      {/if}
    </article>
  {:else}
    <div class="empty card"><Cable size={28}/><h2>No connections yet.</h2><p>Add one above, or start with uploaded documents and meeting minutes.</p></div>
  {/each}
</div>

{#if !workspaceId}
  <div class="section-heading"><h2>Next seven days</h2></div>
  <div class="stack">
    {#each events as event}
      <article class="card row-card"><CalendarDays size={20}/><div class="grow"><h3>{event.title}</h3><p class="help">{new Date(event.starts_at).toLocaleString()}{event.details?.organizer?.emailAddress?.name || event.details?.organizer?.displayName ? ` · ${event.details?.organizer?.emailAddress?.name || event.details?.organizer?.displayName}` : ''}</p></div>{#if event.cancelled}<span class="tag">Cancelled</span>{/if}</article>
    {:else}
      <p class="muted">Connect and synchronize a calendar to see upcoming meetings.</p>
    {/each}
  </div>
{/if}

{#if capabilities}
  <section class="card capability-details">
    <h3>Readiness</h3>
    <div class="readiness-grid">
      {#each [['Google sign-in', capabilities.google?.configured], ['Microsoft sign-in', capabilities.microsoft?.configured], ['Voice capture', capabilities.speech?.configured], ['Email delivery', capabilities.email?.configured], ['Protected credentials', capabilities.encrypted_connections]] as item}
        <div><span>{item[0]}</span><span class="tag" class:green={item[1]}>{item[1] ? 'Ready' : 'Needs setup'}</span></div>
      {/each}
    </div>
    {#if isAdmin}
      <details><summary>Administrator setup</summary>
        <p class="help">Sign-in return addresses to register with each provider:</p>
        <p class="help">Google: <code>{capabilities.google?.callback_url}</code></p>
        <p class="help">Microsoft: <code>{capabilities.microsoft?.callback_url}</code></p>
      </details>
    {/if}
  </section>
{/if}

{#if canManage}<DeliveryPanel connections={rows} emailAvailable={!!capabilities?.email?.configured}/>{/if}
