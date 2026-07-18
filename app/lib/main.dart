import 'package:flutter/material.dart';

import 'screens/home_screen.dart';
import 'services/settings_store.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ClageCookApp());
}

class ClageCookApp extends StatelessWidget {
  const ClageCookApp({super.key, this.repository, this.autoload = true});

  final SettingsRepository? repository;

  /// Tests can disable the initial network request while still rendering the
  /// complete shell. Production always keeps this enabled.
  final bool autoload;

  @override
  Widget build(BuildContext context) {
    final seed = const Color(0xFF4966A6);
    return MaterialApp(
      title: 'Clage Cook',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: seed),
        useMaterial3: true,
        inputDecorationTheme: const InputDecorationTheme(filled: true),
      ),
      darkTheme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
        inputDecorationTheme: const InputDecorationTheme(filled: true),
      ),
      themeMode: ThemeMode.system,
      home: HomeScreen(
        repository: repository ?? SettingsStore(),
        autoload: autoload,
      ),
    );
  }
}
