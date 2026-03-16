import { Component, inject } from '@angular/core';
import { NotificationService } from '../../services/notification.service';

@Component({
  selector: 'app-notification-toast',
  standalone: true,
  template: `
    <div class="fixed top-4 right-4 z-[100] space-y-2 pointer-events-none">
      @for (n of svc.notifications(); track n.id) {
        <div class="pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg border text-sm animate-slide-in-right"
             [class]="typeClass(n.type)">
          <span class="flex-1">{{ n.message }}</span>
          <button (click)="svc.dismiss(n.id)"
                  class="text-current opacity-60 hover:opacity-100 text-lg leading-none">
            &times;
          </button>
        </div>
      }
    </div>
  `,
  styles: [`
    @keyframes slideInRight {
      from { transform: translateX(100%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    .animate-slide-in-right {
      animation: slideInRight 0.25s ease-out;
    }
  `],
})
export class NotificationToastComponent {
  readonly svc = inject(NotificationService);

  typeClass(type: string): string {
    switch (type) {
      case 'success': return 'bg-accent-green/10 border-accent-green/30 text-accent-green';
      case 'error': return 'bg-accent-red/10 border-accent-red/30 text-accent-red';
      case 'warning': return 'bg-accent-yellow/10 border-accent-yellow/30 text-accent-yellow';
      default: return 'bg-accent-blue/10 border-accent-blue/30 text-accent-blue';
    }
  }
}
