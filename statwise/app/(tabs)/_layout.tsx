import React from 'react';
import { Tabs, useRouter, usePathname } from 'expo-router';
import {
  Platform, View, Text, TouchableOpacity, StyleSheet, useWindowDimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';

const TAB_ITEMS = [
  { name: 'index',         path: '/',             title: 'Predictions', icon: 'football-outline'    as const },
  { name: 'insights',      path: '/insights',     title: 'Insights',    icon: 'analytics-outline'   as const },
  { name: 'subscriptions', path: '/subscriptions',title: 'Plans',       icon: 'diamond-outline'     as const },
  { name: 'forum',         path: '/forum',        title: 'Community',   icon: 'chatbubbles-outline' as const },
  { name: 'profile',       path: '/profile',      title: 'Profile',     icon: 'person-outline'      as const },
];

function WebTopNav({ compact }: { compact: boolean }) {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const pathname = usePathname();
  const router = useRouter();

  return (
    <View style={[styles.webNav, { backgroundColor: C.tabBar, borderBottomColor: C.tabBarBorder }]}>
      <TouchableOpacity style={styles.brand} onPress={() => router.push('/')} activeOpacity={0.8}>
        <View style={[styles.brandIcon, { backgroundColor: C.primaryLight }]}>
          <Ionicons name="stats-chart" size={16} color={C.primary} />
        </View>
        {!compact && (
          <Text style={[styles.brandText, { color: C.text }]}>StatWise</Text>
        )}
      </TouchableOpacity>

      <View style={styles.navTabs}>
        {TAB_ITEMS.map(item => {
          const isFocused =
            item.path === '/'
              ? pathname === '/'
              : pathname.startsWith(item.path);
          return (
            <TouchableOpacity
              key={item.name}
              style={[
                styles.navTab,
                compact && styles.navTabCompact,
                isFocused && { borderBottomWidth: 2, borderBottomColor: C.primary },
              ]}
              onPress={() => router.push(item.path as any)}
              activeOpacity={0.75}
            >
              <Ionicons name={item.icon} size={17} color={isFocused ? C.primary : C.textMuted} />
              {!compact && (
                <Text style={[styles.navTabText, { color: isFocused ? C.primary : C.textMuted }]}>
                  {item.title}
                </Text>
              )}
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

export default function TabLayout() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const { width } = useWindowDimensions();

  const isWeb = Platform.OS === 'web';
  const showTopNav = isWeb && width >= 600;
  const compact = isWeb && width < 860;

  const tabBarStyle = {
    backgroundColor: C.tabBar,
    borderTopColor: C.tabBarBorder,
    borderTopWidth: 1,
    height: isWeb ? 84 : undefined,
    paddingBottom: isWeb ? 34 : undefined,
    display: showTopNav ? ('none' as const) : ('flex' as const),
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      {showTopNav && <WebTopNav compact={compact} />}

      <Tabs
        screenOptions={{
          headerShown: false,
          tabBarStyle,
          tabBarActiveTintColor: C.primary,
          tabBarInactiveTintColor: C.textMuted,
          tabBarLabelStyle: { fontSize: 11, fontFamily: 'Inter_500Medium', marginBottom: 2 },
        }}
      >
        <Tabs.Screen
          name="index"
          options={{
            title: 'Predictions',
            tabBarIcon: ({ color, size }) => <Ionicons name="football-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="insights"
          options={{
            title: 'Insights',
            tabBarIcon: ({ color, size }) => <Ionicons name="analytics-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="subscriptions"
          options={{
            title: 'Plans',
            tabBarIcon: ({ color, size }) => <Ionicons name="diamond-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="forum"
          options={{
            title: 'Community',
            tabBarIcon: ({ color, size }) => <Ionicons name="chatbubbles-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="profile"
          options={{
            title: 'Profile',
            tabBarIcon: ({ color, size }) => <Ionicons name="person-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen name="insight-detail"   options={{ href: null }} />
        <Tabs.Screen name="backtesting"      options={{ href: null }} />
        <Tabs.Screen name="privacy-policy"   options={{ href: null }} />
        <Tabs.Screen name="terms-of-service" options={{ href: null }} />
      </Tabs>
    </View>
  );
}

const styles = StyleSheet.create({
  webNav: {
    height: 64,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    borderBottomWidth: 1,
  },
  brand: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginRight: 24,
  },
  brandIcon: {
    width: 30,
    height: 30,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  brandText: {
    fontSize: 18,
    fontFamily: 'Inter_700Bold',
    letterSpacing: -0.5,
  },
  navTabs: {
    flexDirection: 'row',
    flex: 1,
  },
  navTab: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    height: 64,
    borderBottomWidth: 0,
  },
  navTabCompact: {
    paddingHorizontal: 10,
    gap: 0,
  },
  navTabText: {
    fontSize: 14,
    fontFamily: 'Inter_500Medium',
  },
});
