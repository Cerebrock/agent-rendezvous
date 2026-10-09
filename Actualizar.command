#!/bin/zsh
# Doble clic en macOS: consulta fuentes, actualiza el reporte y abre la vista.
cd "${0:A:h}" || exit 1
python3 rendezvous.py scan
scan_status=$?
python3 rendezvous.py report || exit 1
open reports/index.html
if [[ $scan_status -ne 0 ]]; then
  print "Algunas fuentes fallaron. El reporte conserva los datos anteriores y muestra el error."
  read -r "respuesta?Presioná Enter para cerrar. "
fi
exit $scan_status
