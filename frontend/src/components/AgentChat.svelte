<script>
  import { onMount } from 'svelte';
  import { Sparkles, Plus, ArrowRight, MessageSquare, RefreshCw, X, Eye, Check, Clock, Undo2 } from 'lucide-svelte';
  import { api, query } from '../api.js';
  import ContextViewer from './ContextViewer.svelte';
  let { workspaceId = null, projectId = null, models = [], defaultModel = '', allowed = false, canWrite = true } = $props();
  let conversations = $state([]), selected = $state(''), messages = $state([]), text = $state(''), model = $state('');
  let consent = $state(false), error = $state(''), loading = $state(true), submitting = $state(false), run = $state(null), context = $state(null), showContext = $state(false),now=$state(Date.now()),undone=$state([]); 
  let timer,clock, alive = true, generation = 0, requestId = null;
  const running = $derived(run && ['queued','running','cancel_requested','waiting'].includes(run.status));
  const fmt = value => value ? new Date(value).toLocaleString() : '';
  const label = value => String(value || '').replaceAll('_',' ');
  const options = $derived(models.map(m => typeof m === 'string' ? m : m.id));
  const scope = () => ({ ...(workspaceId ? {workspace_id:workspaceId} : {}), ...(projectId ? {project_id:projectId} : {}) });
  onMount(() => { model = options.includes(defaultModel) ? defaultModel : options[0] || ''; load();clock=setInterval(()=>now=Date.now(),1000); return () => { alive=false;clearTimeout(timer);clearInterval(clock);generation++; }; });
  async function load() { try { conversations = await api('/conversations'+query(scope())); } catch(e){error=e.message;} finally{loading=false;} }
  async function select(id) {
    generation++; clearTimeout(timer); selected=id;messages=[];run=null;context=null;error='';loading=true;
    const seq=generation;
    try { const [items,runs]=await Promise.all([api(`/conversations/${id}/messages`),api(`/conversations/${id}/runs`)]); if(seq!==generation)return;messages=items;run=runs.find(r=>['queued','running','waiting','cancel_requested'].includes(r.status))||runs[0]||null;if(running)poll(run.id,seq); }
    catch(e){if(seq===generation)error=e.message;} finally{if(seq===generation)loading=false;}
  }
  function fresh() {generation++;clearTimeout(timer);selected='';messages=[];run=null;context=null;text='';error='';requestId=null;}
  async function poll(id, seq=generation) {
    clearTimeout(timer);
    if(!alive||seq!==generation)return;
    try {
      const result=await api(`/agent-runs/${id}`); if(!alive||seq!==generation)return;run=result;
      if(['queued','running','waiting','cancel_requested'].includes(result.status)) timer=setTimeout(()=>poll(id,seq),1800);
      else { messages=await api(`/conversations/${selected}/messages`);if(result.error)error=result.error;await load(); }
    }catch(e){if(alive&&seq===generation){error=e.message;timer=setTimeout(()=>poll(id,seq),5000);}}
  }
  async function send(event) {
    event.preventDefault();if(!text.trim()||running||submitting||!consent)return;submitting=true;error='';
    try {
      if(!selected){const c=await api('/conversations',{method:'POST',body:{title:text.trim().slice(0,80),...scope()}});selected=c.id;conversations=[c,...conversations];}
      requestId=requestId||crypto.randomUUID();
      run=await api(`/conversations/${selected}/runs`,{method:'POST',body:{content:text,model,external_ai_consent:true,request_id:requestId}});
      requestId=null;text='';context=null;messages=await api(`/conversations/${selected}/messages`);poll(run.id);
    }catch(e){error=e.message;}finally{submitting=false;}
  }
  async function cancel(){try{run=await api(`/agent-runs/${run.id}/cancel`,{method:'POST',body:{}});poll(run.id);}catch(e){error=e.message;}}
  async function undoAction(action){try{await api(`/personal/actions/${action.id}/undo`,{method:'POST',body:{}});undone=[...undone,action.id];}catch(e){error=e.message;}}
  async function preview(){showContext=!showContext;if(!showContext)return;if(!selected){context={message:'Start a conversation to inspect its resolved assistant context.'};return;}try{context=await api(`/conversations/${selected}/context-preview`,{method:'POST',body:{content:text||'Workspace overview'}});}catch(e){error=e.message;}}
</script>
{#if error}<div class="banner error" role="alert">{error}<button class="icon-button" aria-label="Dismiss error" onclick={()=>error=''}><X size={16}/></button></div>{/if}
<div class="chat-layout">
  <aside class="conversation-list"><button class="button wide" onclick={fresh} disabled={submitting||!canWrite}><Plus size={16}/>New conversation</button>{#each conversations as c}<button class="conversation-item" class:chosen={selected===c.id} onclick={()=>select(c.id)} disabled={submitting}><MessageSquare size={15}/><span>{c.title}<small>{fmt(c.updated_at||c.created_at)}</small></span></button>{:else}<p class="muted list-empty">Conversations stay in this space.</p>{/each}</aside>
  <section class="chat-panel" aria-label="Conversation">
    <div class="chat-toolbar"><span><Sparkles size={16}/>{conversations.find(c=>c.id===selected)?.title||'Ask Optimus'}</span><button class="text-button" onclick={preview}><Eye size={15}/>Assistant context</button><label class="inline-label">Model<select bind:value={model} disabled={running||submitting}>{#each options as option}<option value={option}>{option}</option>{/each}</select></label></div>
    {#if showContext}<div class="context-inspector"><h3>What this assistant can use</h3><p class="help">Workspace brief, permitted preferences, skills, tools, and authorized sources. Access is rechecked during execution.</p><ContextViewer {context}/></div>{/if}
    <div class="message-list" aria-live="polite">
      {#if loading}<div class="empty"><RefreshCw size={22}/><p>Loading conversation…</p></div>{:else if !messages.length}<div class="chat-welcome"><span class="feature-icon"><Sparkles size={30}/></span><h2>What would you like to work through?</h2><p>Ask Optimus to investigate a question, compare evidence, or help you prepare for the next decision.</p><div class="suggestion-row"><button onclick={()=>text='What needs my attention today?'}>What needs attention?</button><button onclick={()=>text='Review recent issues and identify what evidence is missing.'}>Review recent issues</button></div></div>{:else}{#each messages as message}<article class="message" class:user-message={message.role==='user'}><header><span>{message.role==='user'?'You':'Optimus'}</span><small>{message.model||fmt(message.created_at)}</small></header><div class="message-content">{message.content}</div>{#if message.source_refs?.length}<details><summary>Evidence · {message.source_refs.length} sources</summary>{#each message.source_refs as ref}<p class="source-ref">{ref.title||ref.filename||ref.document_id||ref.id||ref.kind}{ref.excerpt?`: ${ref.excerpt}`:''}</p>{/each}</details>{/if}</article>{/each}{/if}
    </div>
    {#if run}<div class="agent-progress" role="status"><div class="row-actions">{#if running}<RefreshCw size={16}/>{:else if run.status==='succeeded'}<Check size={16}/>{:else}<Clock size={16}/>{/if}<strong>{label(run.stage||run.status)}</strong><span class="tag">{label(run.status)}</span>{#if running}<button class="button compact" onclick={cancel} disabled={run.status==='cancel_requested'}><X size={14}/>Cancel</button>{/if}</div>{#if run.events?.length}<details><summary>Execution steps</summary>{#each run.events as event}<p class="help">{label(event.stage)}{event.detail?` — ${typeof event.detail==='string'?event.detail:JSON.stringify(event.detail)}`:''}</p>{/each}</details>{/if}</div>{/if}
    {#if run?.context_metadata?.actions?.length}<div class="agent-actions" aria-live="polite">{#each run.context_metadata.actions as action}{#if undone.includes(action.id)}<span class="tag">Action undone</span>{:else if new Date(action.undo_until).getTime()>now}<button class="button compact" onclick={()=>undoAction(action)}><Undo2 size={14}/>Undo {label(action.kind)} · {Math.ceil((new Date(action.undo_until).getTime()-now)/1000)}s</button>{/if}{/each}<small class="help">Automatic changes are also recorded in Assistant activity.</small></div>{/if}
    {#if !allowed}<div class="banner warning">Enable external AI for this installation and workspace before starting an agent.</div>{/if}
    <form class="chat-composer" onsubmit={send}><label for="agent-message" class="sr-only">Message Optimus</label><textarea id="agent-message" bind:value={text} rows="3" placeholder="Ask a question or describe the work…" disabled={submitting||running||!canWrite}></textarea><div class="composer-bottom"><label class="check-row"><input type="checkbox" bind:checked={consent} disabled={!allowed||running}/><span>Use OpenRouter with my authorized conversation and source context.</span></label><button class="button primary" disabled={!text.trim()||!consent||!allowed||!model||submitting||running||!canWrite}>{submitting?'Starting…':'Send'}<ArrowRight size={16}/></button></div></form><p class="chat-footnote">Runs continue in the background. Reopen the conversation to follow progress.</p>
  </section>
</div>
