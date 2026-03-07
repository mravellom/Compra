import { Component, inject } from '@angular/core';
import { OpportunityService } from '../../services/opportunity.service';
import { DecimalPipe } from '@angular/common';

@Component({
  selector: 'app-stats-bar',
  standalone: true,
  imports: [DecimalPipe],
  template: `
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <!-- Total Opportunities -->
      <div class="card">
        <p class="text-xs text-gray-500 uppercase tracking-wider mb-1">Oportunidades</p>
        <p class="text-2xl font-bold text-white">{{ svc.stats().total_opportunities }}</p>
      </div>

      <!-- Avg ROI -->
      <div class="card">
        <p class="text-xs text-gray-500 uppercase tracking-wider mb-1">ROI Promedio</p>
        <p class="text-2xl font-bold" [class]="roiColor(svc.stats().avg_roi)">
          {{ svc.stats().avg_roi * 100 | number:'1.1-1' }}%
        </p>
      </div>

      <!-- Avg Profit -->
      <div class="card">
        <p class="text-xs text-gray-500 uppercase tracking-wider mb-1">Profit Promedio</p>
        <p class="text-2xl font-bold text-accent-green">
          \${{ svc.stats().avg_profit | number:'1.2-2' }}
        </p>
      </div>

      <!-- Top Marketplace -->
      <div class="card">
        <p class="text-xs text-gray-500 uppercase tracking-wider mb-1">Top Marketplace</p>
        <p class="text-2xl font-bold text-accent-blue">
          {{ svc.stats().top_marketplace || '—' }}
        </p>
      </div>
    </div>
  `,
})
export class StatsBarComponent {
  svc = inject(OpportunityService);

  roiColor(roi: number): string {
    if (roi >= 0.4) return 'text-accent-green';
    if (roi >= 0.2) return 'text-accent-yellow';
    return 'text-accent-red';
  }
}
