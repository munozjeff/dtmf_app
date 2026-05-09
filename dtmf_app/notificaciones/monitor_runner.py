"""
Monitor Runner - Modo de Monitoreo Continuo

Este módulo implementa el modo Monitor que permite vigilancia continua
de múltiples perfiles de WhatsApp sin enviar mensajes de campaña.

Características:
- Rotación automática de perfiles
- Número configurable de navegadores simultáneos
- Monitoreo con auto-respuesta
- Duración mínima de 2 minutos por perfil
- Sin necesidad de cargar base de datos
"""

import time
import random
import threading
from datetime import datetime
from src.services.whatsapp_service import WhatsAppService
from src.services.whatsapp_monitor_service import WhatsAppMonitorService


class MonitorRunner:
    """
    Runner para modo Monitor que rota perfiles y monitorea mensajes
    sin enviar campañas.
    """
    
    def __init__(self, config):
        """
        Inicializa el MonitorRunner con la configuración proporcionada.
        
        Args:
            config (dict): Configuración del monitor
                - profiles: Lista de perfiles a monitorear
                - simultaneous: Número de navegadores simultáneos
                - interval: Intervalo entre monitoreos (segundos)
                - auto_reply_text: Texto de auto-respuesta (opcional)
        """
        self.profiles = config.get("profiles", [])
        self.simultaneous = config.get("simultaneous", 1)
        self.interval = config.get("interval", 20)
        self.auto_reply_text = config.get("auto_reply_text")
        # Destinos de notificación: grupo (prioridad) + número celular (respaldo)
        self.monitor_group = config.get("monitor_group")    # Nombre del grupo (prioridad)
        self.monitor_backup = config.get("monitor_backup")  # Número celular (respaldo)
        # Compatibilidad con versiones anteriores que usaban monitor_contact
        if not self.monitor_group and not self.monitor_backup:
            monitor_contact_legacy = config.get("monitor_contact")
            if monitor_contact_legacy:
                self.monitor_group = monitor_contact_legacy
        self.running = True
        self.active_profiles = {}  # {thread_id: (profile, service)}
        
        print(f"\n{'═'*60}")
        print(f"🔍 MODO MONITOR INICIADO")
        print(f"{'═'*60}")
        print(f"Perfiles a monitorear: {len(self.profiles)}")
        print(f"Navegadores simultáneos: {self.simultaneous}")
        print(f"Intervalo de monitoreo: {self.interval}s")
        print(f"Auto-respuesta: {'✅ Activada' if self.auto_reply_text else '❌ Desactivada'}")
        print(f"🥇 Grupo notif. (prioridad): {self.monitor_group if self.monitor_group else '❌ Sin configurar'}")
        print(f"🥈 Celular notif. (respaldo): {self.monitor_backup if self.monitor_backup else '❌ Sin configurar'}")
        print(f"{'═'*60}\n")
    
    def run(self):
        """
        Ejecuta el ciclo principal del monitor rotando entre perfiles.
        """
        if not self.profiles:
            print("[Monitor] ⚠️ No hay perfiles seleccionados para monitorear")
            return
        
        # Pool de perfiles disponibles
        available_profiles = list(self.profiles)
        random.shuffle(available_profiles)
        
        cycle_count = 1
        
        try:
            while self.running and available_profiles:
                print(f"\n{'─'*60}")
                print(f"🔄 CICLO {cycle_count} - Iniciando ronda de monitoreo")
                print(f"{'─'*60}\n")
                
                # Tomar N perfiles para esta ronda
                batch = available_profiles[:self.simultaneous]
                
                print(f"[Monitor] Procesando {len(batch)} perfil(es) en paralelo...")
                for idx, profile in enumerate(batch, 1):
                    print(f"  {idx}. {profile.name}")
                print()
                
                # Procesar cada perfil en paralelo (threads)
                threads = []
                for profile in batch:
                    t = threading.Thread(
                        target=self.monitor_profile,
                        args=(profile,),
                        name=f"Monitor-{profile.name}"
                    )
                    t.start()
                    threads.append(t)
                    time.sleep(1)  # Pequeño delay entre inicios
                
                # Esperar a que terminen todos
                for t in threads:
                    t.join()
                
                print(f"\n[Monitor] ✅ Ronda {cycle_count} completada\n")
                
                # Remover perfiles procesados
                available_profiles = available_profiles[self.simultaneous:]
                
                # Si no quedan más, reiniciar ciclo
                if not available_profiles:
                    print(f"[Monitor] 🔁 Todos los perfiles procesados. Reiniciando ciclo...\n")
                    available_profiles = list(self.profiles)
                    random.shuffle(available_profiles)
                    cycle_count += 1
                
        except KeyboardInterrupt:
            print("\n[Monitor] ⏹️ Monitoreo interrumpido por usuario")
            self.stop()
        except Exception as e:
            print(f"[Monitor] ❌ Error en ciclo principal: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print(f"\n{'═'*60}")
            print(f"🔍 MODO MONITOR FINALIZADO")
            print(f"{'═'*60}\n")
    
    def monitor_profile(self, profile):
        """
        Monitorea un perfil hasta cumplir condiciones de salida:
        - No hay chats pendientes Y han pasado al menos 2 minutos, O
        - Han pasado 2 minutos (tiempo mínimo)
        
        Args:
            profile: Perfil de navegador a monitorear
        """
        service = None
        monitor = None
        profile_name = profile.name
        
        try:
            print(f"[{profile_name}] 🚀 Iniciando monitoreo...")
            
            # Iniciar navegador
            service = WhatsAppService()
            if not service.initialize_driver(profile.path):
                print(f"[{profile_name}] ❌ Error al inicializar servicio")
                return
            
            # Crear monitor con el nombre del perfil y número de notificación
            monitor = WhatsAppMonitorService(
                service.driver,
                notification_group=self.monitor_group,
                notification_backup=self.monitor_backup,
                profile_name=profile_name
            )
            
            start_time = time.time()
            MIN_DURATION = 120  # 2 minutos en segundos
            iteration = 1
            
            print(f"[{profile_name}] ✅ Monitoreo activo (mínimo {MIN_DURATION}s)")
            
            while self.running:
                elapsed = time.time() - start_time
                remaining = max(0, MIN_DURATION - elapsed)
                
                print(f"\n[{profile_name}] ── Iteración {iteration} ──")
                print(f"[{profile_name}] ⏱️ Tiempo transcurrido: {elapsed:.0f}s")
                if remaining > 0:
                    print(f"[{profile_name}] ⏳ Tiempo restante (mínimo): {remaining:.0f}s")
                
                # Ejecutar monitoreo
                try:
                    monitor.monitorear_y_notificar(
                        service,
                        max_time=30,
                        auto_reply_text=self.auto_reply_text
                    )
                except Exception as e:
                    print(f"[{profile_name}] ⚠️ Error en monitoreo: {e}")
                    
                    # CHECK REACTIVO: Verificar si se cerró la sesión
                    try:
                        if service and not service.is_session_active():
                             print(f"[{profile_name}] ⛔ Sesión cerrada detectada durante monitoreo. Marcando BLOQUEADO.")
                             try:
                                 if "BLOQUEADO" not in profile.tags:
                                     profile.tags.append("BLOQUEADO")
                                     profile.save_metadata()
                             except Exception as exc:
                                 print(f"[{profile_name}] Error guardando tag: {exc}")
                             
                             print(f"[{profile_name}] ⏹️ Deteniendo monitoreo de este perfil por bloqueo.")
                             break # Salir del loop de monitoreo
                    except Exception as check_exc:
                         print(f"[{profile_name}] Error verificando sesión: {check_exc}")
                
                # Obtener estado de chats pendientes
                try:
                    chats_no_leidos = monitor.obtener_chats_no_leidos()
                    chats_nuevos = monitor.detectar_nuevos_chats(chats_no_leidos)
                    chats_pendientes = len(chats_nuevos)
                except:
                    chats_pendientes = 0
                
                print(f"[{profile_name}] 📊 Chats pendientes: {chats_pendientes}")
                
                # Verificar condiciones de salida
                if elapsed >= MIN_DURATION:
                    if chats_pendientes == 0:
                        print(f"[{profile_name}] ✅ Sin chats pendientes y tiempo mínimo cumplido")
                        print(f"[{profile_name}] 🔄 Rotando al siguiente perfil...")
                        break
                    else:
                        print(f"[{profile_name}] ℹ️ Tiempo mínimo cumplido, pero hay chats pendientes")
                        print(f"[{profile_name}] ⏳ Continuando monitoreo...")
                
                # Esperar intervalo antes del siguiente ciclo
                print(f"[{profile_name}] 💤 Esperando {self.interval}s hasta próximo monitoreo...\n")
                time.sleep(self.interval)
                iteration += 1
                
        except Exception as e:
            print(f"[{profile_name}] ❌ Error durante monitoreo: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Cerrar navegador
            if service:
                try:
                    print(f"[{profile_name}] 🔚 Cerrando navegador...")
                    service.close()
                except:
                    pass
            
            elapsed_total = time.time() - start_time if 'start_time' in locals() else 0
            print(f"[{profile_name}] ✅ Monitoreo finalizado (duración: {elapsed_total:.0f}s)\n")
    
    def stop(self):
        """
        Detiene el monitor de forma limpia.
        """
        print("\n[Monitor] 🛑 Deteniendo monitoreo...")
        self.running = False
        
        # Cerrar todos los servicios activos
        for thread_id, (profile, service) in self.active_profiles.items():
            try:
                service.close()
            except:
                pass
        
        self.active_profiles.clear()
        print("[Monitor] ✅ Monitoreo detenido correctamente")
