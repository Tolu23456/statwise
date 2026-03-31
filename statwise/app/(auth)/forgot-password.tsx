import React, { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  useColorScheme, ActivityIndicator, Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors } from '@/constants/colors';
import { supabase } from '@/lib/supabase';

export default function ForgotPasswordScreen() {
  const scheme = useColorScheme() ?? 'dark';
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  async function handleReset() {
    if (!email.trim()) {
      Alert.alert('Email Required', 'Please enter your email address.');
      return;
    }
    setLoading(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const { error } = await supabase.auth.resetPasswordForEmail(email.trim());
    setLoading(false);
    if (error) {
      Alert.alert('Error', error.message);
    } else {
      setSent(true);
    }
  }

  return (
    <View style={[styles.container, { backgroundColor: C.background, paddingTop: insets.top + 16 }]}>
      <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
        <Ionicons name="chevron-back" size={24} color={C.text} />
      </TouchableOpacity>

      <View style={styles.content}>
        {sent ? (
          <>
            <Text style={styles.icon}>✉️</Text>
            <Text style={[styles.title, { color: C.text }]}>Check your inbox</Text>
            <Text style={[styles.desc, { color: C.textSecondary }]}>
              We sent a password reset link to{'\n'}
              <Text style={{ color: C.primary, fontFamily: 'Inter_600SemiBold' }}>{email}</Text>
            </Text>
            <TouchableOpacity
              style={[styles.button, { backgroundColor: C.primary }]}
              onPress={() => router.back()}
            >
              <Text style={styles.buttonText}>Back to Login</Text>
            </TouchableOpacity>
          </>
        ) : (
          <>
            <Text style={[styles.title, { color: C.text }]}>Forgot Password?</Text>
            <Text style={[styles.desc, { color: C.textSecondary }]}>
              Enter your email address and we'll send you a link to reset your password.
            </Text>
            <Text style={[styles.label, { color: C.textSecondary }]}>Email Address</Text>
            <TextInput
              style={[styles.input, { backgroundColor: C.inputBg, borderColor: C.border, color: C.text }]}
              placeholder="you@example.com"
              placeholderTextColor={C.placeholder}
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              keyboardType="email-address"
              returnKeyType="done"
              onSubmitEditing={handleReset}
            />
            <TouchableOpacity
              style={[styles.button, { backgroundColor: C.primary, opacity: loading ? 0.7 : 1 }]}
              onPress={handleReset}
              disabled={loading}
            >
              {loading ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Send Reset Link</Text>}
            </TouchableOpacity>
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, paddingHorizontal: 24 },
  backBtn: { width: 40, height: 40, justifyContent: 'center' },
  content: { flex: 1, justifyContent: 'center', paddingBottom: 60 },
  icon: { fontSize: 52, marginBottom: 20, textAlign: 'center' },
  title: { fontSize: 26, fontFamily: 'Inter_700Bold', marginBottom: 12 },
  desc: { fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 22, marginBottom: 28 },
  label: { fontSize: 13, fontFamily: 'Inter_500Medium', marginBottom: 8 },
  input: {
    borderRadius: 12, borderWidth: 1, padding: 14,
    fontSize: 15, fontFamily: 'Inter_400Regular', marginBottom: 20,
  },
  button: { borderRadius: 14, padding: 16, alignItems: 'center' },
  buttonText: { color: '#fff', fontSize: 16, fontFamily: 'Inter_600SemiBold' },
});
