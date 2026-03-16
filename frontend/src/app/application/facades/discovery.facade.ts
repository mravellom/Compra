import { Injectable, inject, signal } from '@angular/core';
import { DiscoveryApiClient } from '../../infrastructure/api-clients/discovery-api.client';
import {
  TrendingProduct,
  NewProduct,
  DiscoveryStats,
  ProductHistory,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class DiscoveryFacade {
  private readonly api = inject(DiscoveryApiClient);

  readonly trending = signal<TrendingProduct[]>([]);
  readonly newProducts = signal<NewProduct[]>([]);
  readonly stats = signal<DiscoveryStats | null>(null);
  readonly productHistory = signal<ProductHistory[]>([]);
  readonly loading = signal(false);
  readonly loadingHistory = signal(false);

  loadTrending(limit = 20, category?: string): void {
    this.loading.set(true);
    this.api.getTrending(limit, category).subscribe({
      next: (data) => {
        this.trending.set(data);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  loadNew(days = 7, limit = 20): void {
    this.api.getNew(days, limit).subscribe({
      next: (data) => this.newProducts.set(data),
    });
  }

  loadStats(): void {
    this.api.getStats().subscribe({
      next: (data) => this.stats.set(data),
    });
  }

  loadProductHistory(productId: number, days = 30): void {
    this.loadingHistory.set(true);
    this.api.getProductHistory(productId, days).subscribe({
      next: (data) => {
        this.productHistory.set(data);
        this.loadingHistory.set(false);
      },
      error: () => this.loadingHistory.set(false),
    });
  }
}
