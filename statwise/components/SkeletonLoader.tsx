import React, { useEffect, useRef } from 'react';
import { View, Animated, StyleSheet } from 'react-native';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';

export function MatchCardSkeleton() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const pulseAnim = useRef(new Animated.Value(0.3)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, { toValue: 1, duration: 800, useNativeDriver: true }),
        Animated.timing(pulseAnim, { toValue: 0.3, duration: 800, useNativeDriver: true }),
      ])
    ).start();
  }, []);

  return (
    <Animated.View style={[styles.card, { backgroundColor: C.card, borderColor: C.border, opacity: pulseAnim }]}>
      <View style={styles.header} />
      <View style={styles.row}>
        <View style={styles.team} />
        <View style={styles.vs} />
        <View style={styles.team} />
      </View>
      <View style={styles.footer} />
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 12, borderWidth: 1, padding: 16, marginBottom: 12 },
  header: { height: 16, width: 80, backgroundColor: '#666', borderRadius: 4, marginBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  team: { height: 40, width: '35%', backgroundColor: '#666', borderRadius: 4 },
  vs: { height: 20, width: 30, backgroundColor: '#666', borderRadius: 4 },
  footer: { height: 32, width: '100%', backgroundColor: '#666', borderRadius: 8, marginTop: 12 },
});
