import { Injectable, signal, computed } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Opportunity, OpportunityDetail, DashboardStats, OpportunityFilters } from '../models/opportunity.model';

const API_BASE = '/api/v1';

@Injectable({ providedIn: 'root' })
export class OpportunityService {
  // State signals
  readonly opportunities = signal<Opportunity[]>([]);
  readonly stats = signal<DashboardStats>({
    total_opportunities: 0,
    avg_roi: 0,
    avg_profit: 0,
    top_marketplace: null,
    last_scan: null,
  });
  readonly selectedOpportunity = signal<OpportunityDetail | null>(null);
  readonly loadingDetail = signal(false);
  readonly loading = signal(false);
  readonly filters = signal<OpportunityFilters>({
    min_roi: 0,
    max_buy_price: 1000,
    marketplaces: [],
  });

  // Computed: filtered opportunities (client-side for instant UI response)
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

  // Computed: available marketplaces from data
  readonly availableMarketplaces = computed(() => {
    const set = new Set<string>();
    this.opportunities().forEach((o) => {
      set.add(o.buy_marketplace);
      set.add(o.sell_marketplace);
    });
    return Array.from(set).sort();
  });

  constructor(private http: HttpClient) {}

  loadOpportunities(): void {
    this.loading.set(true);
    const f = this.filters();
    let params = new HttpParams().set('limit', '200');
    if (f.min_roi > 0) params = params.set('min_roi', f.min_roi.toString());

    this.http.get<Opportunity[]>(`${API_BASE}/opportunities`, { params }).subscribe({
      next: (data) => {
        this.opportunities.set(data);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  loadStats(): void {
    this.http.get<DashboardStats>(`${API_BASE}/opportunities/stats`).subscribe({
      next: (data) => this.stats.set(data),
    });
  }

  triggerScan(): void {
    this.loading.set(true);
    this.http.post<{ new_opportunities: number }>(`${API_BASE}/opportunities/scan`, {}).subscribe({
      next: () => {
        this.loadOpportunities();
        this.loadStats();
      },
      error: () => this.loading.set(false),
    });
  }

  selectOpportunity(id: number): void {
    this.loadingDetail.set(true);
    this.http.get<OpportunityDetail>(`${API_BASE}/opportunities/${id}`).subscribe({
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
}
