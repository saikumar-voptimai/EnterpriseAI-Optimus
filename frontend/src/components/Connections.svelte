<script>
  import { onMount } from 'svelte';
  import { CalendarDays, Cable, Database, Table, FolderOpen, Video, Mail, MessageSquare, RefreshCw, Trash2, ExternalLink, Check, Plus, FileText, Search, Download } from 'lucide-svelte';
  import { api, query } from '../api.js';
  import DeliveryPanel from './DeliveryPanel.svelte';

  // scope: personal (My connections), workspace (shared with one workspace) or
  // organization (Administration → Integrations, configured once for everyone).
  let { workspaceId = null, organization = false, canManage = true, isAdmin = false, workspaces = [] } = $props();
  const scope = $derived(workspaceId ? 'workspace' : organization ? 'organization' : 'personal');

  // Integration kinds as people know them; transport details stay on the server.
  const KINDS = {
    google: { label: 'Google Workspace', icon: CalendarDays, single: true },
    microsoft: { label: 'Microsoft 365', icon: CalendarDays, single: true },
    zoom: { label: 'Zoom', icon: Video, single: true },
    slack_webhook: { label: 'Slack channel', icon: MessageSquare },
    teams_workflow: { label: 'Teams channel', icon: Mail },
    influxdb: { label: 'Process historian', icon: Database },
    postgres: { label: 'SQL database', icon: Table },
    gdrive_folder: { label: 'Google Drive folder', icon: FolderOpen },
  };
  const CATALOG = {
    personal: [
      { provider: 'google', title: 'Google Workspace', text: 'Your calendar, Drive files and Google Meet transcripts.', signIn: true },
      { provider: 'microsoft', title: 'Microsoft 365', text: 'Your Outlook calendar and Teams meeting transcripts.', signIn: true },
      { provider: 'slack_webhook', title: 'Slack', text: 'Personal alerts and updates in a Slack channel.' },
      { provider: 'teams_workflow', title: 'Microsoft Teams', text: 'Personal alerts in a Teams channel or group chat.' },
    ],
    workspace: [
      { provider: 'influxdb', title: 'Process historian', text: 'Time-series plant measurements.', admin: true },
      { provider: 'postgres', title: 'SQL database', text: 'Read-only access to chosen tables.', admin: true },
      { provider: 'gdrive_folder', title: 'Google Drive folder', text: 'Spreadsheets in a shared folder, always current.', admin: true },
      { provider: 'slack_webhook', title: 'Slack', text: 'Team alerts and updates in a Slack channel.' },
      { provider: 'teams_workflow', title: 'Microsoft Teams', text: 'Team alerts in a Teams channel or group chat.' },
    ],
    organization: [
      { provider: 'zoom', title: 'Zoom', text: 'Company Zoom account: recorded meeting transcripts for Meeting Rooms.', admin: true },
    ],
  };
  const EDITORS = {
    influxdb: 'Add a process historian',
    postgres: 'Add a SQL database',
    gdrive_folder: 'Add a Google Drive folder',
    zoom: 'Configure company Zoom',
    slack_webhook: 'Add a Slack channel',
    teams_workflow: 'Add a Teams channel',
  };

  let rows = $state([]), capabilities = $state(null), events = $state([]), resources = $state({});
  let editing = $state(null), form = $state({});
  let busy = $state(false), error = $state(''), notice = $state('');
  let driveFor = $state(null), driveQuery = $state(''), driveFiles = $state([]), driveTarget = $state('');

  const kind = connection => KINDS[connection.provider] || { label: 'Connection', icon: Cable };
  const active = connection => connection.status !== 'disconnected';
  const fmt = value => value ? new Date(value).toLocaleString() : 'Not yet synchronized';
  const canSync = connection => ['google', 'microsoft'].includes(connection.provider);
  const writableWorkspaces = $derived(workspaces.filter(w => ['member', 'manager'].includes(w.membership_role)));
  const configured = provider => rows.some(c => c.provider === provider && active(c));
  const signInReady = provider => capabilities?.[provider]?.configured;

  onMount(load);

  async function load() {
    try {
      const [all, caps] = await Promise.all([api('/connections' + query({ workspace_id: workspaceId })), api('/connections/capabilities')]);
      rows = all.filter(c => scope === 'workspace' ? c.workspace_id === workspaceId
        : scope === 'organization' ? c.provider === 'zoom'
        : !c.workspace_id && c.provider !== 'zoom');
      rows.sort((a, b) => Number(active(b)) - Number(active(a)));
      capabilities = caps;
      if (scope === 'personal') {
        const end = new Date(Date.now() + 7 * 86400000);
        events = await api('/calendar/events' + query({ start: new Date().toISOString(), end: end.toISOString() }));
      }
    } catch (e) { error = e.message; }
  }

  async function run(task, success) {
    busy = true; error = ''; notice = '';
    try { await task(); if (success) notice = success; } catch (e) { error = e.message; } finally { busy = false; }
  }

  const signIn = (provider, extra = {}) => run(async () => {
    const result = await api(`/connections/${provider}/start`, { method: 'POST', body: extra });
    window.location.assign(result.authorization_url);
  });

  function add(item) {
    if (item.signIn) return signIn(item.provider, item.provider === 'microsoft' ? { include_transcripts: !!form.includeTranscripts } : {});
    editing = item.provider;
    form = { name: item.title === 'Slack' || item.title === 'Microsoft Teams' ? `${item.title} alerts` : item.title, port: 5432, sslmode: 'require' };
  }

  function save(event) {
    event.preventDefault();
    run(async () => {
      const provider = editing, f = form;
      const config = provider === 'influxdb' ? { url: f.url, bucket_ids: [], allow_private_network: !!f.allowPrivate }
        : provider === 'postgres' ? { host: f.host, port: Number(f.port) || 5432, database: f.database, sslmode: f.sslmode, allow_private_network: !!f.allowPrivate }
        : provider === 'gdrive_folder' ? { folder_id: f.folder }
        : {};
      const credentials = provider === 'zoom' ? { account_id: f.accountId, client_id: f.clientId, client_secret: f.clientSecret }
        : provider === 'influxdb' ? { token: f.token }
        : provider === 'postgres' ? { username: f.username, password: f.password }
        : provider === 'gdrive_folder' ? { service_account: f.key }
        : { webhook_url: f.url };
      const created = await api('/connections', { method: 'POST', body: { provider, name: f.name, workspace_id: workspaceId, config, credentials } });
      editing = null;
      await load();
      if (['influxdb', 'postgres', 'gdrive_folder'].includes(provider)) await inspect(created);
    }, 'Connection saved. Choose what this workspace may use below.');
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

  const saveConfig = (connection, config) => run(async () => {
    await api(`/connections/${connection.id}`, { method: 'PATCH', body: { config } });
    await load();
  }, 'Selection saved.');

  const toggle = (list, id, enabled) => enabled ? [...new Set([...(list || []), id])] : (list || []).filter(v => v !== id);

  async function openDrive(connection) {
    driveFor = driveFor === connection.id ? null : connection.id;
    driveFiles = []; driveQuery = '';
    if (driveFor) await searchDrive();
  }
  const searchDrive = () => run(async () => { driveFiles = await api(`/connections/${driveFor}/drive/files` + query({ q: driveQuery })); });
  const importFile = file => run(async () => {
    await api(`/connections/${driveFor}/drive/import`, { method: 'POST', body: { file_id: file.id, ...(driveTarget ? { workspace_id: driveTarget } : {}) } });
  }, `${file.name} imported into ${driveTarget ? writableWorkspaces.find(w => w.id === driveTarget)?.name : 'your documents'}.`);
</script>

{#if error}<div class="banner error" role="alert">{error}</div>{/if}
{#if notice}<div class="banner success" role="status"><Check size={16}/>{notice}</div>{/if}

<div class="info-strip">
  <Cable size={20}/>
  <p>{scope === 'workspace'
    ? 'Shared with this workspace: its members and assistant use these within workspace permissions. Data sources are read-only.'
    : scope === 'organization'
      ? 'Configured once for the whole organization. People use these from their own spaces.'
      : 'Only you use these: your calendar, files, meetings and personal alerts. Sign-in details stay protected on the server.'}</p>
</div>

<div class="section-heading"><h2>Connected</h2><button class="text-button" onclick={load}><RefreshCw size={15}/>Refresh</button></div>
<div class="stack">
  {#each rows as connection (connection.id)}
    {@const Icon = kind(connection).icon}
    <article class="card connection-row" class:inactive={!active(connection)}>
      <div class="row-card">
        <span class="feature-icon"><Icon size={18}/></span>
        <div class="grow">
          <h3>{connection.name}</h3>
          <p class="help">{[kind(connection).label, connection.config?.account, connection.config?.folder_name, canSync(connection) && active(connection) ? `Last sync: ${fmt(connection.last_synced_at)}` : ''].filter(Boolean).join(' · ')}</p>
        </div>
        <span class="tag" class:green={connection.status === 'connected'}>{connection.status === 'connected' ? 'Connected' : connection.status === 'pending' ? 'Pending' : connection.status === 'error' ? 'Needs attention' : 'Disconnected'}</span>
        <div class="row-actions">
          {#if canManage && active(connection) && !['zoom', 'slack_webhook', 'teams_workflow'].includes(connection.provider)}
            <button class="button compact" onclick={() => inspect(connection)} disabled={busy}>Settings</button>
          {/if}
          {#if canSync(connection) && active(connection)}<button class="button compact" onclick={() => sync(connection)} disabled={busy}><RefreshCw size={14}/>Sync</button>{/if}
          {#if connection.provider === 'google' && connection.status === 'connected'}<button class="button compact" onclick={() => openDrive(connection)} disabled={busy}><FileText size={14}/>Drive files</button>{/if}
          {#if canManage && active(connection)}<button class="icon-button" aria-label={`Disconnect ${connection.name}`} onclick={() => disconnect(connection)} disabled={busy}><Trash2 size={16}/></button>{/if}
        </div>
      </div>
      {#if connection.last_error && active(connection)}<p class="form-error">{connection.last_error}</p>{/if}

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
      {:else if resources[connection.id]?.tables}
        <div class="connection-resources">
          <fieldset>
            <legend>Tables this workspace may read</legend>
            {#each resources[connection.id].tables as table}
              <label class="check-row"><input type="checkbox" checked={(connection.config?.tables || []).includes(table.id)} onchange={e => saveConfig(connection, { tables: toggle(connection.config?.tables, table.id, e.currentTarget.checked) })} disabled={!isAdmin}/><span>{table.name}{table.kind === 'view' ? ' (view)' : ''}</span></label>
            {:else}
              <p class="help">No readable tables. Grant this database user SELECT on the tables to share.</p>
            {/each}
          </fieldset>
        </div>
      {:else if resources[connection.id]?.folder}
        <div class="connection-resources">
          <h3>{resources[connection.id].folder.name}</h3>
          <p class="help">The assistant reads the latest version of these files each time it needs them.</p>
          {#each resources[connection.id].files.slice(0, 12) as file}
            <div class="row-card compact-row"><FileText size={15}/><span class="grow">{file.name}</span><small class="help">{file.modified_at ? new Date(file.modified_at).toLocaleDateString() : ''}</small></div>
          {:else}
            <p class="help">The folder is empty, or not yet shared with the service account.</p>
          {/each}
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
            <div class="row-card compact-row"><FileText size={15}/><span class="grow">{file.name}<small class="help"> · {new Date(file.modified_at).toLocaleDateString()}</small></span><button class="button compact" onclick={() => importFile(file)} disabled={busy}><Download size={14}/>Import</button></div>
          {:else}
            <p class="help">No matching files.</p>
          {/each}
        </div>
      {/if}
    </article>
  {:else}
    <p class="muted">Nothing connected yet. Add a connection below.</p>
  {/each}
</div>

{#if editing}
  <section class="card connection-editor">
    <h2>{EDITORS[editing]}</h2>
    <form onsubmit={save}>
      <label>Connection name<input bind:value={form.name} required/></label>
      {#if editing === 'influxdb'}
        <label>Server address<input type="url" bind:value={form.url} required/></label>
        <label class="check-row"><input type="checkbox" bind:checked={form.allowPrivate}/><span>The server is on the plant network</span></label>
        <label>Read-only access token<input type="password" bind:value={form.token} required autocomplete="new-password"/></label>
      {:else if editing === 'postgres'}
        <div class="form-grid">
          <label>Server<input bind:value={form.host} required placeholder="db.example.com"/></label>
          <label>Port<input type="number" bind:value={form.port} min="1" max="65535"/></label>
          <label>Database<input bind:value={form.database} required/></label>
          <label>Encryption<select bind:value={form.sslmode}><option value="require">Required</option><option value="verify-full">Required, verify server</option><option value="prefer">Preferred</option><option value="disable">Off (private network only)</option></select></label>
          <label>Read-only user<input bind:value={form.username} required autocomplete="off"/></label>
          <label>Password<input type="password" bind:value={form.password} required autocomplete="new-password"/></label>
        </div>
        <label class="check-row"><input type="checkbox" bind:checked={form.allowPrivate}/><span>The server is on the plant or office network</span></label>
        <p class="help">Use a database user that can only read the tables you want to share. You choose the tables next.</p>
      {:else if editing === 'gdrive_folder'}
        <label>Folder link<input bind:value={form.folder} required placeholder="https://drive.google.com/drive/folders/…"/></label>
        <label>Service account key (JSON)<textarea rows="4" bind:value={form.key} required placeholder={'{"type": "service_account", …}'}></textarea></label>
        <p class="help">Share the folder with the service account's email as <strong>Viewer</strong>. Steps are in the integration guide.</p>
      {:else if editing === 'zoom'}
        <div class="form-grid">
          <label>Account ID<input bind:value={form.accountId} required/></label>
          <label>Client ID<input bind:value={form.clientId} required/></label>
        </div>
        <label>Client secret<input type="password" bind:value={form.clientSecret} required autocomplete="new-password"/></label>
        <p class="help">People can import transcripts of meetings they hosted; administrators can import any.</p>
      {:else}
        <label>{editing === 'slack_webhook' ? 'Slack webhook address' : 'Teams workflow address'}<input type="password" bind:value={form.url} required autocomplete="new-password"/></label>
      {/if}
      <div class="form-actions">
        <button type="button" class="button" onclick={() => editing = null} disabled={busy}>Cancel</button>
        <button class="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save connection'}</button>
      </div>
    </form>
  </section>
{/if}

{#if canManage}
  <div class="section-heading"><h2>Add a connection</h2></div>
  <div class="connection-catalog">
    {#each CATALOG[scope] as item}
      {@const Icon = KINDS[item.provider].icon}
      {@const done = KINDS[item.provider].single && configured(item.provider)}
      {@const blocked = (item.admin && !isAdmin) || (item.signIn && !signInReady(item.provider))}
      <article class="card" class:done>
        <span class="feature-icon"><Icon size={17}/></span>
        <h2>{item.title}</h2>
        <p>{item.text}</p>
        {#if item.provider === 'microsoft' && !done}<label class="check-row"><input type="checkbox" bind:checked={form.includeTranscripts}/><span>Include meeting transcripts</span></label>{/if}
        {#if done}
          <button class="button" disabled><Check size={15}/>Connected</button>
        {:else}
          <button class={item.signIn ? 'button primary' : 'button'} onclick={() => add(item)} disabled={busy || blocked}>{#if item.signIn}<ExternalLink size={15}/>Connect{:else}<Plus size={15}/>Add{/if}</button>
          {#if item.signIn && capabilities && !signInReady(item.provider)}<small class="help">An administrator needs to enable this sign-in first.</small>{/if}
          {#if item.admin && !isAdmin}<small class="help">Added by an administrator.</small>{/if}
        {/if}
      </article>
    {/each}
  </div>
{/if}

{#if scope === 'personal'}
  <div class="section-heading"><h2>Next seven days</h2></div>
  <div class="stack">
    {#each events as event}
      <article class="card row-card compact-row"><CalendarDays size={18}/><div class="grow"><h3>{event.title}</h3><p class="help">{new Date(event.starts_at).toLocaleString()}{event.details?.organizer?.emailAddress?.name || event.details?.organizer?.displayName ? ` · ${event.details?.organizer?.emailAddress?.name || event.details?.organizer?.displayName}` : ''}</p></div>{#if event.cancelled}<span class="tag">Cancelled</span>{/if}</article>
    {:else}
      <p class="muted">Connect and synchronize a calendar to see upcoming meetings.</p>
    {/each}
  </div>
{/if}

{#if scope === 'organization' && capabilities}
  <section class="card capability-details">
    <h3>Readiness</h3>
    <div class="readiness-grid">
      {#each [['Google sign-in', capabilities.google?.configured], ['Microsoft sign-in', capabilities.microsoft?.configured], ['Voice capture', capabilities.speech?.configured], ['Email delivery', capabilities.email?.configured], ['Protected credentials', capabilities.encrypted_connections]] as item}
        <div><span>{item[0]}</span><span class="tag" class:green={item[1]}>{item[1] ? 'Ready' : 'Needs setup'}</span></div>
      {/each}
    </div>
    <details><summary>Sign-in return addresses</summary>
      <p class="help">Register these with each provider's app:</p>
      <p class="help">Google: <code>{capabilities.google?.callback_url}</code></p>
      <p class="help">Microsoft: <code>{capabilities.microsoft?.callback_url}</code></p>
    </details>
  </section>
{/if}

{#if canManage && scope !== 'organization'}<DeliveryPanel connections={rows} emailAvailable={!!capabilities?.email?.configured}/>{/if}
