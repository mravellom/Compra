import { Injectable, inject, signal, computed, OnDestroy } from '@angular/core';
import { OpportunityApiClient } from '../../infrastructure/api-clients/opportunity-api.client';
import { POLL_INTERVAL_MS } from '../../core/config/api.config';
import {
  Opportunity,
  OpportunityDetail,
  DashboardStats,
  OpportunityFilters,
  ScanResult,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class OpportunityFacade implements OnDestroy {
  private readonly api = inject(OpportunityApiClient);
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  // --- State ---
  readonly opportunities = signal<Opportunity[]>([]);
  readonly stats = signal<DashboardStats>({
    total_opportunities: 0,
    avg_roi: 0,
    avg_profit: 0,
    top_marketplace: null,
    last_scan: null,
  });
  readonly selectedOpportunity = signal<OpportunityDetail | null>(null);
  readonly loading = signal(false);
  readonly loadingDetail = signal(false);
  readonly filters = signal<OpportunityFilters>({
    min_roi: 0,
    max_buy_price: 1000,
    marketplaces: [],
  });

  // --- Computed ---
  readonly filteredOpportunities = computed(() => {
    const opps = this.opportunities();
    const f = this.filters();
    return opps.filter((o) => {
      if (o.roi < f.min_roi) return false;
      if (f.max_buy_price && o.buy_price > f.max_buy_price) return false;
      if (f.marketplaces.length > 0) {
        const inFilter =
          f.marketplaces.includes(o.buy_marketplace) ||
          f.marketplaces.includes(o.sell_marketplace);
        if (!inFilter) return false;
      }
      return true;
    });
  });

  readonly availableMarketplaces = computed(() => {
    const set = new Set<string>();
    this.opportunities().forEach((o) => {
      set.add(o.buy_marketplace);
      set.add(o.sell_marketplace);
    });
    return Array.from(set).sort();
  });

  // --- Actions ---
  loadOpportunities(silent = false): void {
    if (!silent) this.loading.set(true);
    const f = this.filters();
    const apiFilters: Partial<OpportunityFilters> = { limit: 200 };
    if (f.min_roi > 0) apiFilters.min_roi = f.min_roi;

    this.api.getAll(apiFilters).subscribe({
      next: (data) => {
        this.opportunities.set(data);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  loadStats(): void {
    this.api.getStats().subscribe({
      next: (data) => this.stats.set(data),
    });
  }

  triggerScan(): void {
    this.loading.set(true);
    this.api.triggerScan().subscribe({
      next: () => {
        this.loadOpportunities();
        this.loadStats();
      },
      error: () => this.loading.set(false),
    });
  }

  selectOpportunity(id: number): void {
    this.loadingDetail.set(true);
    this.api.getById(id).subscribe({
      next: (data) => {
        this.selectedOpportunity.set(data);
        this.loadingDetail.set(false);
      },
      error: () => this.loadingDetail.set(false),
    });
  }

  clearSelection(): void {
    this.selectedOpportunity.set(null);
  }

  updateFilters(partial: Partial<OpportunityFilters>): void {
    this.filters.update((current) => ({ ...current, ...partial }));
  }

  startPolling(): void {
    this.stopPolling();
    this.pollTimer = setInterval(() => {
      this.loadOpportunities(true);
      this.loadStats();
    }, POLL_INTERVAL_MS);
  }

  stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }
}
