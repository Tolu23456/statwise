/**
 * Global network-status indicator (plan step 3.4).
 *
 * Subscribes to lib/network (browser online/offline + Network Information
 * API) and renders a single honest banner:
 *   offline → "You're offline — showing the last data loaded"
 *   slow    → "Slow connection — updating less often"
 * Renders nothing in the normal case, so it costs one state on every screen
 * it's mounted on and no layout shift when things are fine.
 */
import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';
import { currentNetworkState, subscribeNetwork, type NetworkState } from '@/lib/network';

export function OfflineBanner() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const [net, setNet] = useState<NetworkState>(() => currentNetworkState());

  useEffect(() => subscribeNetwork(setNet), []);

  if (net.online && !net.slow) return null;
  const offline = !net.online;
  const fg = offline ? C.danger : C.warning;
  const bg = offline ? C.dangerLight : C.warningLight;

  return (
    <View
      accessibilityLiveRegion="polite"
      style={[styles.banner, { backgroundColor: bg, borderBottomColor: fg }]}
    >
      <Ionicons
        name={offline ? 'cloud-offline-outline' : 'pulse-outline'}
        size={14}
        color={fg}
      />
      <Text style={[styles.text, { color: fg }]}>
        {offline
          ? "You're offline — showing the last data loaded"
          : 'Slow connection — updating less often'}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
  },
  text: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
});
