import { Injectable, inject, signal } from '@angular/core';
import { CategoryApiClient } from '../../infrastructure/api-clients/category-api.client';
import { NotificationService } from '../../shared/services/notification.service';
import { CategoryArbitrage, CategoryRefreshResult } from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class CategoryFacade {
  private readonly api = inject(CategoryApiClient);
  private readonly notifications = inject(NotificationService);

  readonly categories = signal<CategoryArbitrage[]>([]);
  readonly loading = signal(false);
  readonly refreshing = signal(false);

  load(minRoi?: number, minScore?: number, limit = 50): void {
    this.loading.set(true);
    this.api.getArbitrage(minRoi, minScore, limit).subscribe({
      next: (data) => {
        this.categories.set(data);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  refresh(): void {
    this.refreshing.set(true);
    this.api.refresh().subscribe({
      next: (result) => {
        this.refreshing.set(false);
        this.notifications.success(
          `${result.categories_analyzed} categorias analizadas`,
        );
        this.load();
      },
      error: () => this.refreshing.set(false),
    });
  }
}
