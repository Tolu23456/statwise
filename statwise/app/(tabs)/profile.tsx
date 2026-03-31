import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  useColorScheme, Switch, Alert, ActivityIndicator, Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { useQuery } from '@tanstack/react-query';
import { Colors, TierBadgeColors } from '@/constants/colors';
import { supabase, ReferralCode } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';

export default function ProfileScreen() {
  const scheme = useColorScheme() ?? 'dark';
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { user, profile, signOut, refreshProfile } = useAuth();

  const [editingName, setEditingName] = useState(false);
  const [displayName, setDisplayName] = useState(profile?.display_name ?? '');
  const [savingName, setSavingName] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const topInset = Platform.OS === 'web' ? 67 : insets.top;
  const tierColors = TierBadgeColors[profile?.current_tier ?? 'Free Tier'];

  const { data: referral } = useQuery<ReferralCode | null>({
    queryKey: ['referral', user?.id],
    queryFn: async () => {
      if (!user) return null;
      const { data } = await supabase
        .from('referral_codes')
        .select('*')
        .eq('user_id', user.id)
        .single();
      return data as ReferralCode ?? null;
    },
    enabled: !!user,
  });

  async function saveDisplayName() {
    if (!displayName.trim() || !user) return;
    setSavingName(true);
    await supabase
      .from('user_profiles')
      .update({ display_name: displayName.trim(), username: displayName.trim() })
      .eq('id', user.id);
    await refreshProfile();
    setSavingName(false);
    setEditingName(false);
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
  }

  async function pickAndUploadPhoto() {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Permission Required', 'Please allow access to your photo library.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: true, aspect: [1, 1], quality: 0.7,
    });
    if (result.canceled || !result.assets[0]) return;
    if (!user) return;

    setUploadingPhoto(true);
    try {
      const asset = result.assets[0];
      const ext = asset.uri.split('.').pop() ?? 'jpg';
      const fileName = `${user.id}.${ext}`;
      const response = await fetch(asset.uri);
      const blob = await response.blob();

      const { error: uploadError } = await supabase.storage
        .from('profile-pictures')
        .upload(fileName, blob, { upsert: true, contentType: `image/${ext}` });

      if (!uploadError) {
        const { data: urlData } = supabase.storage
          .from('profile-pictures')
          .getPublicUrl(fileName);
        await supabase
          .from('user_profiles')
          .update({ avatar_url: urlData.publicUrl })
          .eq('id', user.id);
        await refreshProfile();
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      }
    } catch (e) {
      console.warn('Photo upload error:', e);
      Alert.alert('Upload Failed', 'Could not upload profile picture.');
    } finally {
      setUploadingPhoto(false);
    }
  }

  async function handleSignOut() {
    Alert.alert('Sign Out', 'Are you sure you want to sign out?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign Out',
        style: 'destructive',
        onPress: async () => {
          setSigningOut(true);
          await signOut();
        },
      },
    ]);
  }

  function getInitials(name?: string) {
    if (!name) return '?';
    return name.split(' ').map(n => n[0]).join('').toUpperCase().substring(0, 2);
  }

  function copyReferralCode() {
    if (!referral?.code) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    Alert.alert('Referral Code Copied', `Your code: ${referral.code}\n\nShare this with friends to earn rewards!`);
  }

  return (
    <View style={[styles.container, { backgroundColor: C.background }]}>
      <View style={[styles.header, { paddingTop: topInset + 8 }]}>
        <Text style={[styles.title, { color: C.text }]}>Profile</Text>
      </View>

      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingBottom: insets.bottom + (Platform.OS === 'web' ? 84 : 80) },
        ]}
        showsVerticalScrollIndicator={false}
      >
        <View style={[styles.profileCard, { backgroundColor: C.card, borderColor: C.border }]}>
          <TouchableOpacity style={styles.avatarWrapper} onPress={pickAndUploadPhoto} disabled={uploadingPhoto}>
            {profile?.avatar_url ? (
              <Image source={{ uri: profile.avatar_url }} style={styles.avatar} contentFit="cover" />
            ) : (
              <View style={[styles.avatarPlaceholder, { backgroundColor: C.primaryLight }]}>
                <Text style={[styles.avatarInitials, { color: C.primary }]}>
                  {getInitials(profile?.display_name)}
                </Text>
              </View>
            )}
            {uploadingPhoto ? (
              <View style={styles.avatarOverlay}>
                <ActivityIndicator color="#fff" size="small" />
              </View>
            ) : (
              <View style={[styles.avatarOverlay, { backgroundColor: 'rgba(0,0,0,0.4)' }]}>
                <Ionicons name="camera" size={16} color="#fff" />
              </View>
            )}
          </TouchableOpacity>

          {editingName ? (
            <View style={styles.nameEditRow}>
              <TextInput
                style={[styles.nameInput, { borderColor: C.primary, color: C.text, backgroundColor: C.inputBg }]}
                value={displayName}
                onChangeText={setDisplayName}
                autoFocus
                returnKeyType="done"
                onSubmitEditing={saveDisplayName}
              />
              <TouchableOpacity
                style={[styles.saveBtn, { backgroundColor: C.primary }]}
                onPress={saveDisplayName}
                disabled={savingName}
              >
                {savingName
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Ionicons name="checkmark" size={18} color="#fff" />
                }
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.cancelBtn, { backgroundColor: C.border }]}
                onPress={() => { setEditingName(false); setDisplayName(profile?.display_name ?? ''); }}
              >
                <Ionicons name="close" size={18} color={C.textSecondary} />
              </TouchableOpacity>
            </View>
          ) : (
            <TouchableOpacity style={styles.nameRow} onPress={() => setEditingName(true)}>
              <Text style={[styles.profileName, { color: C.text }]}>{profile?.display_name ?? 'User'}</Text>
              <Ionicons name="pencil" size={14} color={C.textSecondary} style={{ marginLeft: 6 }} />
            </TouchableOpacity>
          )}

          <Text style={[styles.profileEmail, { color: C.textSecondary }]}>{user?.email}</Text>
          <View style={[styles.tierBadge, { backgroundColor: tierColors.bg }]}>
            <Text style={[styles.tierText, { color: tierColors.text }]}>{profile?.current_tier ?? 'Free Tier'}</Text>
          </View>
        </View>

        {referral && (
          <View style={[styles.section, { backgroundColor: C.card, borderColor: C.border }]}>
            <Text style={[styles.sectionTitle, { color: C.text }]}>Referral Program</Text>
            <Text style={[styles.sectionDesc, { color: C.textSecondary }]}>
              Invite friends and earn rewards when they subscribe.
            </Text>
            <TouchableOpacity
              style={[styles.codeRow, { backgroundColor: C.inputBg, borderColor: C.border }]}
              onPress={copyReferralCode}
            >
              <Text style={[styles.codeText, { color: C.primary }]}>{referral.code}</Text>
              <Ionicons name="copy-outline" size={18} color={C.textSecondary} />
            </TouchableOpacity>
            <Text style={[styles.referralCount, { color: C.textSecondary }]}>
              {referral.total_referrals} friends referred
            </Text>
          </View>
        )}

        <View style={[styles.section, { backgroundColor: C.card, borderColor: C.border }]}>
          <Text style={[styles.sectionTitle, { color: C.text }]}>Settings</Text>
          <View style={[styles.settingRow, { borderBottomColor: C.border }]}>
            <Ionicons name="notifications-outline" size={20} color={C.textSecondary} />
            <Text style={[styles.settingLabel, { color: C.text }]}>Notifications</Text>
            <Switch
              value={profile?.notifications ?? true}
              onValueChange={async (val) => {
                if (!user) return;
                await supabase.from('user_profiles').update({ notifications: val }).eq('id', user.id);
                await refreshProfile();
              }}
              trackColor={{ false: C.border, true: C.primary }}
              thumbColor="#fff"
            />
          </View>
          <View style={styles.settingRow}>
            <Ionicons name="shield-checkmark-outline" size={20} color={C.textSecondary} />
            <Text style={[styles.settingLabel, { color: C.text }]}>Account Verified</Text>
            <Ionicons
              name={user?.email_confirmed_at ? 'checkmark-circle' : 'alert-circle'}
              size={20}
              color={user?.email_confirmed_at ? C.success : C.warning}
            />
          </View>
        </View>

        <TouchableOpacity
          style={[styles.signOutBtn, { borderColor: C.danger, backgroundColor: C.dangerLight }]}
          onPress={handleSignOut}
          disabled={signingOut}
        >
          {signingOut
            ? <ActivityIndicator color={C.danger} size="small" />
            : <>
                <Ionicons name="log-out-outline" size={20} color={C.danger} />
                <Text style={[styles.signOutText, { color: C.danger }]}>Sign Out</Text>
              </>
          }
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: { paddingHorizontal: 16, paddingBottom: 12 },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold' },
  scroll: { paddingHorizontal: 16, paddingTop: 4 },
  profileCard: {
    borderRadius: 20, borderWidth: 1, padding: 24,
    alignItems: 'center', marginBottom: 16,
  },
  avatarWrapper: { position: 'relative', marginBottom: 14 },
  avatar: { width: 80, height: 80, borderRadius: 40 },
  avatarPlaceholder: {
    width: 80, height: 80, borderRadius: 40,
    alignItems: 'center', justifyContent: 'center',
  },
  avatarInitials: { fontSize: 28, fontFamily: 'Inter_700Bold' },
  avatarOverlay: {
    position: 'absolute', bottom: 0, right: 0,
    width: 26, height: 26, borderRadius: 13,
    alignItems: 'center', justifyContent: 'center',
  },
  nameRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 4 },
  profileName: { fontSize: 20, fontFamily: 'Inter_700Bold' },
  nameEditRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4, width: '100%' },
  nameInput: {
    flex: 1, borderRadius: 10, borderWidth: 1.5,
    padding: 10, fontSize: 16, fontFamily: 'Inter_500Medium',
  },
  saveBtn: { width: 38, height: 38, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  cancelBtn: { width: 38, height: 38, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  profileEmail: { fontSize: 14, fontFamily: 'Inter_400Regular', marginBottom: 10 },
  tierBadge: { paddingHorizontal: 14, paddingVertical: 5, borderRadius: 20 },
  tierText: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  section: { borderRadius: 16, borderWidth: 1, padding: 16, marginBottom: 14 },
  sectionTitle: { fontSize: 17, fontFamily: 'Inter_700Bold', marginBottom: 6 },
  sectionDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', marginBottom: 14 },
  codeRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    borderRadius: 12, borderWidth: 1, paddingHorizontal: 14, paddingVertical: 12, marginBottom: 8,
  },
  codeText: { fontSize: 20, fontFamily: 'Inter_700Bold', letterSpacing: 3 },
  referralCount: { fontSize: 13, fontFamily: 'Inter_400Regular' },
  settingRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12,
    borderBottomWidth: 1,
  },
  settingLabel: { flex: 1, fontSize: 15, fontFamily: 'Inter_400Regular' },
  signOutBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
    borderRadius: 16, borderWidth: 1.5, padding: 16, marginBottom: 8,
  },
  signOutText: { fontSize: 16, fontFamily: 'Inter_600SemiBold' },
});
