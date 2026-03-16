import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-loading-spinner',
  standalone: true,
  template: `
    <div class="flex items-center justify-center" [class]="containerClass">
      <div class="border-2 border-accent-blue/30 border-t-accent-blue rounded-full animate-spin"
           [class]="sizeClass"></div>
    </div>
  `,
})
export class LoadingSpinnerComponent {
  @Input() size: 'sm' | 'md' | 'lg' = 'md';
  @Input() containerClass = 'py-20';

  get sizeClass(): string {
    switch (this.size) {
      case 'sm': return 'w-5 h-5';
      case 'lg': return 'w-12 h-12';
      default: return 'w-8 h-8';
    }
  }
}
