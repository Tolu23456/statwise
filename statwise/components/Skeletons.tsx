import React, { useEffect, useRef } from 'react';
import { Animated, StyleSheet, View } from 'react-native';
import { useTheme } from '@/context/ThemeContext';
import { Colors } from '@/constants/colors';
import {
  SHIMMER_CYCLE_MS,
  SKELETON_OPACITY_MAX,
  SKELETON_OPACITY_MIN,
} from '@/lib/animCore';

/**
 * Shared skeleton kit: one pulse loop + block primitive, and per-screen
 * placeholder shapes (insight article card, forum chat row). The pulse
 * timing comes from animCore so every skeleton in the app breathes in
 * sync. Loading states should always show structure, not a bare spinner.
 */

type Opacity = Animated.AnimatedInterpolation<number>;

/** The shared breathe loop: min → max → min, forever. */
export function useSkeletonPulse(): Opacity {
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 1,
          duration: SHIMMER_CYCLE_MS,
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 0,
          duration: SHIMMER_CYCLE_MS,
          useNativeDriver: true,
        }),
      ]),
    ).start();
    return () => pulse.stopAnimation();
  }, [pulse]);
  return pulse.interpolate({
    inputRange: [0, 1],
    outputRange: [SKELETON_OPACITY_MIN, SKELETON_OPACITY_MAX],
  });
}

export function SkeletonBlock({
  width,
  height,
  radius = 8,
  opacity,
  border,
}: {
  width: number | string;
  height: number;
  radius?: number;
  opacity: Opacity;
  border: string;
}) {
  return (
    <Animated.View
      style={{
        width: width as number,
        height,
        borderRadius: radius,
        backgroundColor: border,
        opacity,
      }}
    />
  );
}

/** Placeholder shaped like an Exclusive Insights article card. */
export function SkeletonInsightCard() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const pulse = useSkeletonPulse();
  return (
    <View style={[styles.card, { backgroundColor: C.card, borderColor: C.border }]}>
      <View style={styles.rowBetween}>
        <SkeletonBlock width={92} height={20} radius={20} opacity={pulse} border={C.border} />
        <SkeletonBlock width={64} height={14} opacity={pulse} border={C.border} />
      </View>
      <SkeletonBlock width="70%" height={20} radius={6} opacity={pulse} border={C.border} />
      <SkeletonBlock width="100%" height={13} radius={5} opacity={pulse} border={C.border} />
      <SkeletonBlock width="85%" height={13} radius={5} opacity={pulse} border={C.border} />
      <View style={styles.rowBetween}>
        <SkeletonBlock width={70} height={13} radius={5} opacity={pulse} border={C.border} />
        <SkeletonBlock width={110} height={30} radius={8} opacity={pulse} border={C.border} />
      </View>
    </View>
  );
}

/** Placeholder shaped like a forum chat row (avatar + bubble lines). */
export function SkeletonChatRow() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const pulse = useSkeletonPulse();
  return (
    <View style={styles.chatRow}>
      <SkeletonBlock width={34} height={34} radius={17} opacity={pulse} border={C.border} />
      <View style={[styles.bubble, { backgroundColor: C.card, borderColor: C.border }]}>
        <SkeletonBlock width={96} height={12} radius={5} opacity={pulse} border={C.border} />
        <SkeletonBlock width="90%" height={12} radius={5} opacity={pulse} border={C.border} />
        <SkeletonBlock width="60%" height={12} radius={5} opacity={pulse} border={C.border} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 16, borderWidth: 1, padding: 16, marginBottom: 12,
    gap: 10,
  },
  rowBetween: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
  },
  chatRow: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginBottom: 14,
  },
  bubble: {
    flex: 1, borderRadius: 14, borderWidth: 1, padding: 12, gap: 8,
  },
});
