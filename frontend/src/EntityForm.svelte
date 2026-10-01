<script>
  let { fields = [], initial = {}, submit, cancel, label = 'Save', busy = false, error = '' } = $props();
  let values = $state({});
  $effect(() => { values = Object.fromEntries(fields.map(field => [field.key, initial[field.key] ?? (field.type === 'check' ? false : field.type === 'multiselect' ? [] : '')])); });
  function send(event) { event.preventDefault(); submit(values); }
</script>
<form onsubmit={send} class="entity-form">
  {#each fields as field}
    {#if field.type === 'check'}
      <label class="check-row"><input type="checkbox" bind:checked={values[field.key]} disabled={busy || field.disabled}/><span>{field.label}{#if field.help}<small>{field.help}</small>{/if}</span></label>
    {:else if field.type === 'multiselect'}
      <fieldset><legend>{field.label}</legend>{#each field.options || [] as option}<label class="check-row compact"><input type="checkbox" value={option.value} bind:group={values[field.key]} disabled={busy}/>{option.label}</label>{/each}{#if !field.options?.length}<p class="muted">No options available yet.</p>{/if}</fieldset>
    {:else}
      <label>{field.label}{#if field.required}<span class="required"> *</span>{/if}
        {#if field.type === 'textarea'}<textarea rows={field.rows || 5} bind:value={values[field.key]} required={field.required} placeholder={field.placeholder || ''} disabled={busy} maxlength={field.maxlength}></textarea>
        {:else if field.type === 'select'}<select bind:value={values[field.key]} required={field.required} disabled={busy || field.disabled}>{#if field.placeholder}<option value="">{field.placeholder}</option>{/if}{#each field.options || [] as option}<option value={option.value}>{option.label}</option>{/each}</select>
        {:else}<input type={field.type || 'text'} bind:value={values[field.key]} required={field.required} placeholder={field.placeholder || ''} disabled={busy} min={field.min} max={field.max} minlength={field.minlength} maxlength={field.maxlength} autocomplete={field.autocomplete || 'off'}/>{/if}
        {#if field.help}<small class="help">{field.help}</small>{/if}
      </label>
    {/if}
  {/each}
  {#if error}<p class="form-error" role="alert">{error}</p>{/if}
  <div class="form-actions"><button type="button" class="button" onclick={cancel} disabled={busy}>Cancel</button><button class="button primary" disabled={busy}>{busy ? 'Working…' : label}</button></div>
</form>
