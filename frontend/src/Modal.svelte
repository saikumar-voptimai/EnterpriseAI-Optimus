<script>
  import { onMount } from 'svelte';
  import { X } from 'lucide-svelte';
  let { title, description = '', close, busy = false, children } = $props();
  let element;
  onMount(() => { element.showModal(); });
</script>
<dialog bind:this={element} oncancel={(event) => { event.preventDefault(); if (!busy) close(); }} aria-labelledby="dialog-heading" class="modal">
  <div class="modal-header"><div><span class="eyebrow">V-OPTIMAISE</span><h2 id="dialog-heading">{title}</h2></div><button type="button" class="icon-button" aria-label="Close dialog" onclick={close} disabled={busy}><X size={20}/></button></div>
  {#if description}<p class="modal-description">{description}</p>{/if}
  {@render children()}
</dialog>
