import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-progress-bar',
  standalone: true,
  template: `
    <div class="flex items-center gap-1.5 text-[11px] text-gray-500">
      @if (label) { <span>{{ label }}</span> }
      <div class="h-1.5 bg-surface-600 rounded-full overflow-hidden" [class]="widthClass">
        <div class="h-full rounded-full transition-all" [class]="barColor" [style.width.%]="value"></div>
      </div>
    </div>
  `,
})
export class ProgressBarComponent {
  @Input() label?: string;
  @Input() value = 0;
  @Input() color: 'blue' | 'green' | 'red' | 'yellow' | 'auto' = 'blue';
  @Input() widthClass = 'w-12';

  get barColor(): string {
    if (this.color === 'auto') {
      return this.value > 70 ? 'bg-accent-red' : 'bg-accent-green';
    }
    return `bg-accent-${this.color}`;
  }
}
