import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet],
  template: `
    <div class="min-h-screen bg-surface-900">
      <!-- Top nav -->
      <nav class="bg-surface-800 border-b border-surface-600 px-6 py-3 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 bg-accent-blue rounded-lg flex items-center justify-center text-sm font-bold">R</div>
          <span class="text-lg font-semibold text-white">Radar de Oportunidades</span>
        </div>
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
export class AppComponent {}
