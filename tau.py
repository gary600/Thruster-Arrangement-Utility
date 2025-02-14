import numpy as np
from scipy.optimize import linprog
import json
import matplotlib
from matplotlib import cm
import matplotlib.pyplot as plt
import typing as t
import click

DEFAULT_RESOLUTION = 100  # Runtime is O(n^2) with respect to resolution!
DEFAULT_MAX_THRUSTS = [-2.9, 3.71]  # Lifted from the BlueRobotics public performance data (kgf)
# coefficients of the quadratic approximating current draw as a function of thrust in the forward direction in the form:
# ax^2 + bx + c
DEFAULT_FWD_CURRENT = [.741, 1.89, -.278]
DEFAULT_REV_CURRENT = [1.36, 2.04, -.231]  # reverse direction
DEFAULT_MAX_CURRENT = 22


class Thruster3D:
    def __init__(self, x, y, z, theta, phi, max_thrusts, fwd_current, rev_current):
        self.pos = np.array([x, y, z])
        self.max_thrusts = max_thrusts
        self.fwd_current = fwd_current
        self.rev_current = rev_current

        # Calculate the unit vector in the direction specified by theta and phi
        theta = np.radians(theta)
        phi = np.radians(phi)
        self.orientation = np.array([
            np.sin(phi) * np.cos(theta),
            np.sin(phi) * np.sin(theta),
            np.cos(phi)
        ])

    def torque(self):
        return np.cross(self.pos, self.orientation)


def transform_orientations(thrusters: t.List[Thruster3D], target_dir: np.ndarray):
    """
    Calculate the maximum force achievable in (exclusively) the given direction by the given set of constraints
    :param thrusters: A list of Thruster3D objects representing the available thrusters
    :param target_dir: A 3d vector in the target direction
    :return: The maximum thrust force in kgf
    """
    target_dir = target_dir / np.linalg.norm(target_dir)  # Make target_dir a unit vector

    orientations = np.empty((3, len(thrusters)))  # Create an empty 2d array to hold the orientation of each thruster
    for i in range(len(thrusters)):
        thruster = thrusters[i]
        orientations[..., i] = thruster.orientation

    new_bases = np.empty((3, 3))  # Create an empty 3x3 change of basis matrix
    new_bases[..., 0] = target_dir  # The first basis is our target direction

    if not (target_dir[1] == 0 and target_dir[2] == 0):  # Make sure the cross product computed below isn't 0
        second_basis = np.cross(target_dir, np.array([1, 0, 0]))  # Choose a second basis parallel the first
    else:
        second_basis = np.cross(target_dir, np.array([0, 1, 0]))
    second_basis /= np.linalg.norm(second_basis)  # Make the second basis a unit vector

    new_bases[..., 1] = second_basis
    third_basis = np.cross(target_dir, second_basis)  # Calculate a third basis perpendicular the first two
    third_basis /= np.linalg.norm(third_basis)  # Make the third basis a unit vector
    new_bases[..., 2] = third_basis

    # Invert the matrix. The original matrix maps (1, 0, 0) onto the target direction. We want a matrix
    # that maps the target direction onto (1, 0, 0).
    inverse_transform = np.linalg.inv(new_bases)

    # Calculate the transformation with matrix_vector multiplication
    transformed_orientations = inverse_transform.dot(orientations).transpose()

    return transformed_orientations


def get_max_thrust(transformed_orientations, t_constraints: np.ndarray, max_current: int):
    # First Simplex run. Find the maximum thrust in the desired direction
    objective = []
    left_of_equality = []

    thrusts_y = []
    thrusts_z = []

    torques_x = []
    torques_y = []
    torques_z = []

    bounds = []

    for i, orientation in enumerate(transformed_orientations):
        objective.append(-orientation[0])  # Algorithm minimizes only, so the objective function needs to be negated.

        thrusts_y.append(orientation[1])
        thrusts_z.append(orientation[2])

        torques_x.append(t_constraints[i][0])
        torques_y.append(t_constraints[i][1])
        torques_z.append(t_constraints[i][2])

        bounds.append(DEFAULT_MAX_THRUSTS)  # TODO: Use the custom thruster specs

    left_of_equality.append(thrusts_y)
    left_of_equality.append(thrusts_z)

    left_of_equality.append(torques_x)
    left_of_equality.append(torques_y)
    left_of_equality.append(torques_z)

    right_of_equality = [
        0,  # y thrust
        0,  # z thrust
        0,  # x torque
        0,  # y torque
        0,  # z torque
    ]

    max_thrust_result = linprog(c=objective, A_ub=None, b_ub=None, A_eq=left_of_equality, b_eq=right_of_equality,
                                bounds=bounds, method="highs")

    max_thrust = -.999 * max_thrust_result.fun  # some sort of precision/numerical error makes this bullshit necessary

    # Second Simplex run. Find the minimum current that produces the same thrust as the first result
    objective_mincurrent = []
    left_of_equality_mincurrent = []

    thrusts_x_mincurrent = []
    thrusts_y_mincurrent = []
    thrusts_z_mincurrent = []

    torques_x_mincurrent = []
    torques_y_mincurrent = []
    torques_z_mincurrent = []

    bounds_mincurrent = []

    for i, orientation in enumerate(transformed_orientations, start=0):
        # duplicate each thruster into a forward and a reverse half-thruster
        objective_mincurrent.append(1)  # minimize the thrust of all thrusters weighted equally
        objective_mincurrent.append(1)

        thrusts_x_mincurrent.append(orientation[0])
        thrusts_x_mincurrent.append(-orientation[0])  # duplicate, reversed thruster

        thrusts_y_mincurrent.append(orientation[1])
        thrusts_y_mincurrent.append(-orientation[1])

        thrusts_z_mincurrent.append(orientation[2])
        thrusts_z_mincurrent.append(-orientation[2])

        torques_x_mincurrent.append(t_constraints[i][0])
        torques_x_mincurrent.append(-t_constraints[i][0])

        torques_y_mincurrent.append(t_constraints[i][1])
        torques_y_mincurrent.append(-t_constraints[i][1])

        torques_z_mincurrent.append(t_constraints[i][2])
        torques_z_mincurrent.append(-t_constraints[i][2])

        bounds_mincurrent.append((0, 3.71))
        bounds_mincurrent.append((0, 2.90))

    left_of_equality_mincurrent.append(thrusts_x_mincurrent)
    left_of_equality_mincurrent.append(thrusts_y_mincurrent)
    left_of_equality_mincurrent.append(thrusts_z_mincurrent)

    left_of_equality_mincurrent.append(torques_x_mincurrent)
    left_of_equality_mincurrent.append(torques_y_mincurrent)
    left_of_equality_mincurrent.append(torques_z_mincurrent)

    right_of_equality_mincurrent = [
        max_thrust,  # x thrust constrained to previous maximum
        0,  # y thrust
        0,  # z thrust
        0,  # x torque
        0,  # y torque
        0,  # z torque
    ]

    min_current_result = linprog(c=objective_mincurrent, A_ub=None, b_ub=None, A_eq=left_of_equality_mincurrent,
                                 b_eq=right_of_equality_mincurrent, bounds=bounds_mincurrent, method="highs")

    min_current_duplicated_array = min_current_result.x

    min_current_true_array = []
    for i in range(0, len(min_current_duplicated_array) - 1, 2):
        min_current_true_array.append(min_current_duplicated_array[i] - min_current_duplicated_array[
            i + 1])  # combine half-thrusters into full thrusters

    current_quadratic = [0] * 3

    for thrust in min_current_true_array:
        if thrust >= 0:  # use the forward thrust coefficients
            # TODO: Use customized thruster specs
            current_quadratic[0] += DEFAULT_FWD_CURRENT[0] * thrust ** 2  # a * t^2
            current_quadratic[1] += DEFAULT_FWD_CURRENT[1] * thrust  # b * t
            current_quadratic[2] += DEFAULT_FWD_CURRENT[2]  # c
        else:  # use the reverse thrust coefficients
            # TODO: Use customized thruster specs
            current_quadratic[0] += DEFAULT_REV_CURRENT[0] * (-thrust) ** 2
            current_quadratic[1] += DEFAULT_REV_CURRENT[1] * (-thrust)
            current_quadratic[2] += DEFAULT_REV_CURRENT[2]

    current_quadratic[2] -= max_current  # ax^2 + bx + c = I -> ax^2 + bx + (c-I) = 0

    # solve quadratic, take the proper point, and clamp it to a maximum of 1.0
    thrust_multiplier = min(1., max(np.roots(current_quadratic)))

    thrust_value = 0
    for i in range(0, len(min_current_true_array)):
        thrust_value += min_current_true_array[i] * transformed_orientations[i][
            0]  # get total thrust in target direction

    return thrust_value * thrust_multiplier


#####################################
# Yaw, pitch, roll code
#####################################
def calc_max_yaw_pitch_roll(thrusters, torque_constraints):
    torque_x = []
    torque_y = []
    torque_z = []
    orientation = []
    constraint3 = []
    constraint4 = []
    constraint5 = []
    right_equalities = [0, 0, 0, 0, 0]
    bounds = []

    for i in range(len(torque_constraints)):
        torque_x.append(torque_constraints[i][0])
        torque_y.append(torque_constraints[i][1])
        torque_z.append(torque_constraints[i][2])
        thruster = thrusters[i]
        orientation.append(thruster.orientation)
        constraint3.append(thruster.orientation[0])
        constraint4.append(thruster.orientation[1])
        constraint5.append(thruster.orientation[2])
        torques = [torque_x, torque_y, torque_z]
        bounds.append(DEFAULT_MAX_THRUSTS)  # TODO: Use customized thruster specs

    for i in range(len(torques)): #?????
        torques = [torque_x, torque_y, torque_z]
        objective = [torques[i]]

        torques.pop(i)
        constraint1 = torques[0]
        constraint2 = torques[1]

        left_equalities = [constraint1, constraint2, constraint3, constraint4, constraint5]

        res = linprog(c=objective, A_ub=None, b_ub=None, A_eq=left_equalities, b_eq=right_equalities,
                      bounds=bounds, method="highs")

        print(res)


# The main entry point of the program
# All the Click decorators define various options that can be passed in on the command line
@click.command()
@click.option("--thrusters", "-t", default="thrusters.json", help="file containing thruster specifications")
@click.option("--resolution", "-r",
              default=DEFAULT_RESOLUTION,
              help="resolution of the thrust calculation, runtime is O(n^2) with respect to this!"
)
@click.option("--max-current", "-c", default=DEFAULT_MAX_CURRENT, help="maximum thruster current draw in amps")
def main(thrusters, resolution: int, max_current: int):
    # This doc comment becomes the description text for the --help menu
    """
    tau - the thruster arrangement utility
    """

    # Read the thruster transforms input JSON file
    # Wrap this in a try-except FileNotFoundError block to print a nicer error message
    with open(thrusters) as f:  # `with` blocks allow you to open files safely without risking corrupting them on crash
        thrusters_raw = json.load(f)

    # Convert loaded JSON data into Thruster3D objects
    thrusters: t.List[Thruster3D] = [
        Thruster3D(
            thruster_raw['x'],
            thruster_raw['y'],
            thruster_raw['z'],
            thruster_raw['theta'],
            thruster_raw['phi'],
            # Optional thruster parameters: dict.get is used to provide a default value if the key doesn't exist
            # TODO: Use customized thruster specs in the calculations
            thruster_raw.get("max_thrusts", DEFAULT_MAX_THRUSTS),
            thruster_raw.get("fwd_current", DEFAULT_FWD_CURRENT),
            thruster_raw.get("rev_current", DEFAULT_REV_CURRENT)
        )
        for thruster_raw in thrusters_raw
    ]

    # Calculate the torque constrains which will apply to every iteration
    torque_constraints = [thruster.torque() for thruster in thrusters]

    # get_max_thrust(thrusters, np.array([1, 0, 0]), torque_constraints)

    # I have no idea what np.meshgrid does
    u, v = np.mgrid[0:2 * np.pi:resolution * 1j, 0:np.pi: resolution / 2 * 1j]
    np.empty(np.shape(u))
    mesh_x = np.empty(np.shape(u))
    mesh_y = np.empty(np.shape(u))
    mesh_z = np.empty(np.shape(u))

    # Iterate over each vertex and calculate the max thrust in that direction
    # Note: Should probably be its own function, then it can be optimized more (i.e. Numba)
    max_rho = 0
    for i in range(np.shape(u)[0]):
        for j in range(np.shape(u)[1]):
            z = np.cos(u[i][j]) * np.sin(v[i][j])
            y = np.sin(u[i][j]) * np.sin(v[i][j])
            x = np.cos(v[i][j])
            transformed_orientations = transform_orientations(thrusters, np.array([x, y, z]))
            # TODO: Need some way to carry over the customized thruster specs with the transformed orientations
            rho = get_max_thrust(transformed_orientations, torque_constraints, max_current)
            mesh_x[i][j] = x * rho
            mesh_y[i][j] = y * rho
            mesh_z[i][j] = z * rho
            max_rho = max(max_rho, rho)

    max_rho = np.ceil(max_rho)

    color_index = np.sqrt(mesh_x**2 + mesh_y**2 + mesh_z**2)

    norm = matplotlib.colors.Normalize(vmin=color_index.min(), vmax=color_index.max())

    # Start plotting results
    matplotlib.use('TkAgg')
    fig = plt.figure()

    # Set up plot: 3d orthographic plot with ROV axis orientation
    ax = fig.add_subplot(111, projection='3d', proj_type='ortho')

    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=30, azim=-150)

    ax.set_xlim((max_rho, -max_rho))  # Invert x axis
    ax.set_ylim((-max_rho, max_rho))
    ax.set_zlim((max_rho, -max_rho))  # Invert y axis

    ax.set_xlabel('X (Surge)')
    ax.set_ylabel('Y (Sway)')
    ax.set_zlabel('Z (Heave)')

    # Draw some "axes" so it's clear where (0, 0, 0) is
    ax.plot((-max_rho, max_rho), (0, 0), (0, 0), c="black")
    ax.plot((0, 0), (-max_rho, max_rho), (0, 0), c="black")
    ax.plot((0, 0), (0, 0), (-max_rho, max_rho), c="black")

    # Plot the locations and orientations of the thrusters
    # NOTE: Consider merging this all into the function call below to avoid creating all these unwieldy variable names?
    thrusterloc_x = [2 * thruster.pos[0] for thruster in thrusters]
    thrusterloc_y = [2 * thruster.pos[1] for thruster in thrusters]
    thrusterloc_z = [2 * thruster.pos[2] for thruster in thrusters]

    thrusterdir_x = [2 * thruster.orientation[0] for thruster in thrusters]
    thrusterdir_y = [2 * thruster.orientation[1] for thruster in thrusters]
    thrusterdir_z = [2 * thruster.orientation[2] for thruster in thrusters]

    ax.quiver(thrusterloc_x, thrusterloc_y, thrusterloc_z, thrusterdir_x, thrusterdir_y, thrusterdir_z, color="black")

    # Plot the zero-torque maximum thrust in each direction
    color_index_modified = (color_index - color_index.min()) / (color_index.max() - color_index.min())
    ax.plot_surface(
        mesh_x, mesh_y, mesh_z,
        alpha=0.6, facecolors=cm.jet(color_index_modified), edgecolors='w', linewidth=0
    )

    # Create a legend mapping the colors of the thrust plot to thrust values
    color_range = color_index.max() - color_index.min()
    m = cm.ScalarMappable(cmap=plt.cm.jet, norm=norm)
    plt.colorbar(m, ticks=[
        color_index.min(),
        color_index.min() + color_range/4,
        color_index.min() + color_range/2,
        color_index.min() + 3*color_range/4,
        color_index.max()
    ])

    # Show plot
    plt.show()

    # Print max yaw, pitch, and roll
    calc_max_yaw_pitch_roll(thrusters, torque_constraints)


if __name__ == "__main__":  # Only run the main function the program is being run directly, not imported
    main()  # Click autofills the parameters to this based on the program's command-line arguments
