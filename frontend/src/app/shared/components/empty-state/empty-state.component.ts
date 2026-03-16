import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-empty-state',
  standalone: true,
  template: `
    <div class="text-center py-20">
      <p class="text-gray-600 text-lg">{{ message }}</p>
      @if (subtitle) {
        <p class="text-gray-700 text-sm mt-2">{{ subtitle }}</p>
      }
    </div>
  `,
})
export class EmptyStateComponent {
  @Input() message = 'Sin datos';
  @Input() subtitle?: string;
}
