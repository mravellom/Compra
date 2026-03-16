import { Component, Input } from '@angular/core';
import { DecimalPipe } from '@angular/common';

@Component({
  selector: 'app-score-badge',
  standalone: true,
  imports: [DecimalPipe],
  template: `
    <div class="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold border-2"
         [class]="borderClass">
      {{ value | number:'1.0-0' }}
    </div>
  `,
})
export class ScoreBadgeComponent {
  @Input({ required: true }) value!: number;

  get borderClass(): string {
    if (this.value >= 75) return 'border-accent-green text-accent-green';
    if (this.value >= 50) return 'border-accent-yellow text-accent-yellow';
    return 'border-gray-600 text-gray-400';
  }
}
