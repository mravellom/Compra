import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { OpportunityService } from './services/opportunity.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet],
  template: `
    <div class="min-h-screen bg-surface-900">
      <!-- Top nav -->
      <nav class="bg-surface-800 border-b border-surface-600 px-6 py-3 flex items-center justify-between">
        <button (click)="runScan()"
                class="flex items-center gap-3 hover:opacity-80 active:scale-95 transition-all cursor-pointer"
                [disabled]="svc.loading()"
                title="Ejecutar scraping">
          <div class="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold"
               [class]="svc.loading() ? 'bg-accent-blue/50 animate-pulse' : 'bg-accent-blue'">
            @if (svc.loading()) {
              <svg class="w-4 h-4 animate-spin text-white" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
              </svg>
            } @else {
              R
            }
          </div>
          <span class="text-lg font-semibold text-white">Radar de Oportunidades</span>
        </button>
        <div class="flex items-center gap-4 text-sm text-gray-400">
          <span class="flex items-center gap-1.5">
            <span class="w-2 h-2 bg-accent-green rounded-full animate-pulse"></span>
            Live
          </span>
        </div>
      </nav>
      <router-outlet />
    </div>
  `,
})
export class AppComponent {
  svc = inject(OpportunityService);

  runScan(): void {
    this.svc.triggerScan();
  }
}
