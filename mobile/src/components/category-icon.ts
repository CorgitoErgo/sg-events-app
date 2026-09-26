import type Ionicons from '@expo/vector-icons/Ionicons';
import type { ComponentProps } from 'react';

type IconName = ComponentProps<typeof Ionicons>['name'];

/** The app owns icons (and could own labels) so the taxonomy can change without a release. */
export const CATEGORY_ICONS: Record<string, IconName> = {
  career_fair: 'briefcase',
  career_dev: 'trending-up',
  networking: 'people',
  tech_startup: 'rocket',
  community: 'home',
  volunteering: 'heart',
  workshop_class: 'construct',
  talks: 'mic',
  arts_culture: 'color-palette',
  music: 'musical-notes',
  family_kids: 'happy',
  sports_fitness: 'bicycle',
  nature_outdoors: 'leaf',
  food_markets: 'restaurant',
  health_wellness: 'fitness',
  faith_festivals: 'sparkles',
  education_open_house: 'school',
  youth: 'star',
  seniors: 'accessibility',
};

export function categoryIcon(id: string | undefined): IconName {
  return (id && CATEGORY_ICONS[id]) || 'calendar';
}
