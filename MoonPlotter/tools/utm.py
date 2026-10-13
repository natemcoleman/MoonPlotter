import numpy as np
def utm_forward(lat, lon, lon0, a=6378137.0, f=1/298.257222101, k0=0.9996, E0=500000.0):
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    al = [n/2 - 2*n**2/3 + 5*n**3/16 + 41*n**4/180,
          13*n**2/48 - 3*n**3/5 + 557*n**4/1440,
          61*n**3/240 - 103*n**4/140,
          49561*n**4/161280]
    phi = np.radians(lat); dl = np.radians(lon - lon0)
    c = 2*np.sqrt(n)/(1+n)
    t = np.sinh(np.arctanh(np.sin(phi)) - c*np.arctanh(c*np.sin(phi)))
    xi_ = np.arctan2(t, np.cos(dl)); eta_ = np.arctanh(np.sin(dl)/np.sqrt(1+t*t))
    xi = xi_.copy(); eta = eta_.copy()
    for j, aj in enumerate(al, 1):
        xi += aj*np.sin(2*j*xi_)*np.cosh(2*j*eta_)
        eta += aj*np.cos(2*j*xi_)*np.sinh(2*j*eta_)
    return E0 + k0*A*eta, k0*A*xi
if __name__ == '__main__':
    print(utm_forward(43.642567, -79.387139, -81))   # CN Tower -> 630084, 4833438
    print(utm_forward(39.284190181, -120.301550266, -117))
