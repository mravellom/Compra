import { Injectable, inject, signal } from '@angular/core';
import { TrendApiClient } from '../../infrastructure/api-clients/trend-api.client';
import { NotificationService } from '../../shared/services/notification.service';
import { Trend, TrendScanResult } from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class TrendFacade {
  private readonly api = inject(TrendApiClient);
  private readonly notifications = inject(NotificationService);

  readonly trends = signal<Trend[]>([]);
  readonly breakouts = signal<Trend[]>([]);
  readonly selectedTrend = signal<Trend | null>(null);
  readonly loading = signal(false);
  readonly scanning = signal(false);
  readonly lastScanResult = signal<TrendScanResult | null>(null);

  loadTrends(topN = 20): void {
    this.loading.set(true);
    this.api.getAll(topN).subscribe({
      next: (data) => {
        this.trends.set(data.trends);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  loadBreakouts(limit = 10): void {
    this.api.getBreakouts(limit).subscribe({
      next: (data) => this.breakouts.set(data.trends),
    });
  }

  loadByProduct(productId: number): void {
    this.api.getByProduct(productId).subscribe({
      next: (data) => this.selectedTrend.set(data),
      error: () => this.selectedTrend.set(null),
    });
  }

  scan(limit = 100): void {
    this.scanning.set(true);
    this.api.scan(limit).subscribe({
      next: (result) => {
        this.lastScanResult.set(result);
        this.scanning.set(false);
        this.notifications.success(
          `Scan completo: ${result.trends_detected} tendencias, ${result.breakouts} breakouts`,
        );
        this.loadTrends();
        this.loadBreakouts();
      },
      error: () => this.scanning.set(false),
    });
  }
}
