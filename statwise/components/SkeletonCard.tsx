import React, { useRef, useEffect } from 'react';
import { View, StyleSheet, Animated } from 'react-native';
import { useTheme } from '@/context/ThemeContext';
import { Colors } from '@/constants/colors';

function SkeletonBlock({
  width,
  height,
  radius = 8,
  opacity,
  border,
}: {
  width: number | string;
  height: number;
  radius?: number;
  opacity: Animated.AnimatedInterpolation<number>;
  border: string;
}) {
  return (
    <Animated.View
      style={{ width: width as any, height, borderRadius: radius, backgroundColor: border, opacity }}
    />
  );
}

export function SkeletonCard() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const shimmer = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(shimmer, { toValue: 1, duration: 800, useNativeDriver: true }),
        Animated.timing(shimmer, { toValue: 0, duration: 800, useNativeDriver: true }),
      ]),
    ).start();
    return () => shimmer.stopAnimation();
  }, [shimmer]);

  const opacity = shimmer.interpolate({ inputRange: [0, 1], outputRange: [0.35, 0.75] });

  return (
    <View style={[styles.card, { backgroundColor: C.card, borderColor: C.border }]}>
      <View style={styles.header}>
        <SkeletonBlock width={80} height={20} radius={20} opacity={opacity} border={C.border} />
        <SkeletonBlock width={60} height={14} radius={6} opacity={opacity} border={C.border} />
      </View>
      <View style={styles.titleRow}>
        <SkeletonBlock width="65%" height={18} radius={6} opacity={opacity} border={C.border} />
      </View>
      <View style={styles.badgeRow}>
        <SkeletonBlock width={100} height={28} radius={8} opacity={opacity} border={C.border} />
        <SkeletonBlock width={60} height={16} radius={6} opacity={opacity} border={C.border} />
      </View>
      <View style={styles.confRow}>
        <SkeletonBlock width={130} height={14} radius={6} opacity={opacity} border={C.border} />
      </View>
      <SkeletonBlock width="100%" height={6} radius={3} opacity={opacity} border={C.border} />
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 16, borderWidth: 1, padding: 16, marginBottom: 12, overflow: 'hidden', gap: 0,
  },
  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10,
  },
  titleRow: { marginBottom: 12 },
  badgeRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12,
  },
  confRow: { marginBottom: 8 },
});
