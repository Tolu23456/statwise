import React from 'react';
import { StyleProp, ViewStyle } from 'react-native';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { ENTRANCE_DURATION_MS, entranceDelay } from '@/lib/animCore';

/**
 * Staggered entrance wrapper for list rows: rows slide up + fade in,
 * delayed by their index (capped in animCore so deep rows never
 * waterfall). Runs on the UI thread via Reanimated layout animations —
 * no JS-frame work per row. On platforms where layout animations are
 * unsupported it degrades to a plain view (children still render).
 *
 * The FIRST row only fades (no vertical travel): a slide-from-above
 * would start it ~20px over whatever sits above the list (the category
 * chip bar) — reported overlap on the Insights screen.
 */
export function AnimatedEntrance({
  index,
  children,
  style,
}: {
  index: number;
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const entering =
    index === 0
      ? FadeIn.duration(ENTRANCE_DURATION_MS).delay(entranceDelay(index))
      : FadeInDown.duration(ENTRANCE_DURATION_MS).delay(entranceDelay(index));
  return (
    <Animated.View entering={entering} style={style}>
      {children}
    </Animated.View>
  );
}
