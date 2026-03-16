import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-stat-card',
  standalone: true,
  template: `
    <div class="card">
      <p class="text-xs text-gray-500 uppercase tracking-wider mb-1">{{ label }}</p>
      <p class="text-2xl font-bold" [class]="valueColor">{{ displayValue }}</p>
      @if (subtitle) {
        <p class="text-xs text-gray-600 mt-1">{{ subtitle }}</p>
      }
    </div>
  `,
})
export class StatCardComponent {
  @Input({ required: true }) label!: string;
  @Input({ required: true }) displayValue!: string;
  @Input() valueColor = 'text-white';
  @Input() subtitle?: string;
}
