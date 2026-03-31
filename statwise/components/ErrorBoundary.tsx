import React, { Component } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { reloadAppAsync } from 'expo';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColorScheme } from 'react-native';
import { Colors } from '@/constants/colors';

function ErrorFallback() {
  const insets = useSafeAreaInsets();
  const scheme = useColorScheme() ?? 'dark';
  const C = Colors[scheme];

  return (
    <View style={[styles.container, { backgroundColor: C.background, paddingTop: insets.top + 20, paddingBottom: insets.bottom + 20 }]}>
      <Text style={[styles.icon, { color: C.danger }]}>⚠</Text>
      <Text style={[styles.title, { color: C.text }]}>Something went wrong</Text>
      <Text style={[styles.message, { color: C.textSecondary }]}>
        The app encountered an unexpected error. Tap below to restart.
      </Text>
      <TouchableOpacity
        style={[styles.button, { backgroundColor: C.primary }]}
        onPress={() => reloadAppAsync()}
      >
        <Text style={styles.buttonText}>Restart App</Text>
      </TouchableOpacity>
    </View>
  );
}

type State = { hasError: boolean };

export class ErrorBoundary extends Component<{ children: React.ReactNode }, State> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    console.error('ErrorBoundary caught:', error);
  }

  render() {
    if (this.state.hasError) return <ErrorFallback />;
    return this.props.children;
  }
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  icon: { fontSize: 48, marginBottom: 16 },
  title: { fontSize: 22, fontWeight: '700', marginBottom: 12, textAlign: 'center' },
  message: { fontSize: 15, textAlign: 'center', lineHeight: 22, marginBottom: 32 },
  button: { paddingHorizontal: 32, paddingVertical: 14, borderRadius: 12 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
