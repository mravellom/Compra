import { Component, inject, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OpportunityService } from '../../services/opportunity.service';
import { DecimalPipe } from '@angular/common';

@Component({
  selector: 'app-sidebar',
  standalone: true,
  imports: [FormsModule, DecimalPipe],
  template: `
    <!-- Backdrop (mobile / collapsed) -->
    @if (open) {
      <div class="fixed inset-0 z-20 bg-black/50 lg:hidden" (click)="open = false"></div>
    }

    <aside
      class="bg-surface-800 border-r border-surface-600 p-5 flex flex-col gap-6 overflow-y-auto
             fixed z-30 inset-y-0 left-0 w-72 transition-transform duration-200 lg:static lg:z-auto"
      [class]="open ? 'translate-x-0' : '-translate-x-full lg:hidden'">

      <!-- Header with close -->
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider">Filtros</h2>
        <button (click)="open = false"
                class="text-gray-500 hover:text-white transition-colors text-xl leading-none lg:hidden">
          &times;
        </button>
      </div>

      <!-- ROI Slider -->
      <div>
        <label class="flex justify-between text-sm mb-2">
          <span class="text-gray-400">ROI Minimo</span>
          <span class="text-accent-green font-medium">{{ minRoi * 100 | number:'1.0-0' }}%</span>
        </label>
        <input type="range" [min]="0" [max]="1" [step]="0.05"
               [(ngModel)]="minRoi" (ngModelChange)="applyFilters()" />
        <div class="flex justify-between text-[10px] text-gray-600 mt-1">
          <span>0%</span>
          <span>100%</span>
        </div>
      </div>

      <!-- Price Range -->
      <div>
        <label class="flex justify-between text-sm mb-2">
          <span class="text-gray-400">Precio max. compra</span>
          <span class="text-white font-medium">\${{ maxPrice | number:'1.0-0' }}</span>
        </label>
        <input type="range" [min]="0" [max]="2000" [step]="50"
               [(ngModel)]="maxPrice" (ngModelChange)="applyFilters()" />
        <div class="flex justify-between text-[10px] text-gray-600 mt-1">
          <span>$0</span>
          <span>$2,000</span>
        </div>
      </div>

      <!-- Marketplaces -->
      <div>
        <p class="text-sm text-gray-400 mb-3">Marketplaces</p>
        <div class="flex flex-col gap-2">
          @for (mp of svc.availableMarketplaces(); track mp) {
            <label class="flex items-center gap-2.5 cursor-pointer group">
              <input type="checkbox" [checked]="isMarketplaceSelected(mp)"
                     (change)="toggleMarketplace(mp)"
                     class="w-4 h-4 rounded bg-surface-700 border-surface-600 text-accent-blue
                            focus:ring-accent-blue/30 focus:ring-offset-0" />
              <span class="text-sm text-gray-400 group-hover:text-gray-200 transition-colors capitalize">
                {{ mp }}
              </span>
            </label>
          }
          @if (svc.availableMarketplaces().length === 0) {
            <p class="text-xs text-gray-600 italic">Sin datos aun</p>
          }
        </div>
      </div>

      <!-- Scan button -->
      <div class="mt-auto pt-4 border-t border-surface-600">
        <button (click)="svc.triggerScan()" [disabled]="svc.loading()"
                class="btn-primary w-full flex items-center justify-center gap-2 disabled:opacity-50">
          @if (svc.loading()) {
            <span class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
            Escaneando...
          } @else {
            Escanear ahora
          }
        </button>
      </div>
    </aside>
  `,
})
export class SidebarComponent {
  svc = inject(OpportunityService);
  open = false;

  minRoi = 0;
  maxPrice = 1000;
  selectedMarketplaces: Set<string> = new Set();

  toggle(): void {
    this.open = !this.open;
  }

  isMarketplaceSelected(mp: string): boolean {
    return this.selectedMarketplaces.has(mp);
  }

  toggleMarketplace(mp: string): void {
    if (this.selectedMarketplaces.has(mp)) {
      this.selectedMarketplaces.delete(mp);
    } else {
      this.selectedMarketplaces.add(mp);
    }
    this.applyFilters();
  }

  applyFilters(): void {
    this.svc.updateFilters({
      min_roi: this.minRoi,
      max_buy_price: this.maxPrice,
      marketplaces: Array.from(this.selectedMarketplaces),
    });
  }
}
