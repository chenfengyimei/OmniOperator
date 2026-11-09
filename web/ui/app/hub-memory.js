import { $ } from './dom.js?v=20261103u5';
import { loadMemory } from '/memory/memory.js?v=20261103u5';
export function loadMemoryHub() {
  return loadMemory($('#hub-memory-content'));
}
