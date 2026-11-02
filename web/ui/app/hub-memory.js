import { $ } from './dom.js?v=20261102u2';
import { loadMemory } from '/memory/memory.js?v=20261102u2';
export function loadMemoryHub() {
  return loadMemory($('#hub-memory-content'));
}
